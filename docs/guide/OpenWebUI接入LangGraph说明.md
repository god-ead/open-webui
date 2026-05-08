# OpenWebUI 接入 LangGraph 说明

本文档用于说明当前 OpenWebUI 项目如何与 LangGraph 相接。目标是先理解调用链和改造边界，再决定采用哪种接入方案。

本文只整理方案与学习路径，不直接修改业务代码。

## 一、结论先行

对当前项目而言，最推荐的接入方式是：

1. 将 LangGraph 应用单独做成一个后端服务。
2. 在 LangGraph 服务外层包一层 OpenAI 兼容接口。
3. 让 OpenWebUI 把这个服务当成一个 OpenAI 兼容模型供应商。
4. 前端仍使用 OpenWebUI 原有聊天页面、用户体系、会话存储、文件上传、权限控制。

也就是说，OpenWebUI 不需要直接理解 LangGraph 的图结构。OpenWebUI 只需要知道：

- 有哪些模型可选：`GET /v1/models`
- 如何发起对话：`POST /v1/chat/completions`
- 如果要流式输出，则返回 OpenAI SSE 格式

LangGraph 负责：

- 多步骤推理
- 工具调用
- 工作流编排
- 状态流转
- 复杂业务逻辑

OpenWebUI 负责：

- 登录与用户管理
- 聊天界面
- 模型选择
- 会话管理
- 文件上传
- RAG、工具、权限等现有平台能力

## 二、当前 OpenWebUI 的聊天调用链

### 1. 前端接口常量

文件：[src/lib/constants.ts](/home/zhongjinyan/project/program/open-webui/src/lib/constants.ts:1)

开发模式下，前端运行在 `5173`，后端运行在 `8080`：

```ts
export const WEBUI_HOSTNAME = browser ? (dev ? `${location.hostname}:8080` : ``) : '';
export const WEBUI_BASE_URL = browser ? (dev ? `http://${WEBUI_HOSTNAME}` : ``) : ``;
export const WEBUI_API_BASE_URL = `${WEBUI_BASE_URL}/api/v1`;
export const OPENAI_API_BASE_URL = `${WEBUI_BASE_URL}/openai`;
```

含义：

- 前端页面访问地址通常是 `http://localhost:5173`。
- 前端请求后端接口时，会访问 `http://localhost:8080`。
- OpenAI 兼容管理接口走 `/openai`。
- 通用聊天接口走 `/api/chat/completions` 或 `/api/v1/chat/completions`。

### 2. OpenAI 兼容模型配置

文件：[backend/open_webui/config.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/config.py:1046)

OpenWebUI 后端通过这些配置维护 OpenAI 兼容服务：

```python
ENABLE_OPENAI_API
OPENAI_API_KEY
OPENAI_API_BASE_URL
OPENAI_API_KEYS
OPENAI_API_BASE_URLS
OPENAI_API_CONFIGS
```

含义：

- `OPENAI_API_BASE_URLS` 可以配置多个 OpenAI 兼容服务地址。
- `OPENAI_API_KEYS` 与服务地址一一对应。
- `OPENAI_API_CONFIGS` 可配置每个服务的启用状态、模型列表、前缀、认证方式等。

如果 LangGraph 服务暴露成 OpenAI 兼容接口，就可以作为这里的一个 `OPENAI_API_BASE_URLS` 接入。

### 3. 模型列表获取

文件：[backend/open_webui/routers/openai.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/routers/openai.py:546)

OpenWebUI 会请求每个外部 OpenAI 兼容服务的模型列表：

```python
GET {OPENAI_API_BASE_URL}/models
```

相关逻辑：

- `get_all_models_responses()` 遍历所有配置的 OpenAI 兼容服务。
- `get_models_request()` 请求每个服务的 `/models`。
- `get_all_models()` 合并多个服务返回的模型。
- 每个模型会带上 `urlIdx`，后续聊天时用它找到对应服务。

因此，LangGraph 适配服务必须至少提供：

```http
GET /v1/models
```

返回格式建议兼容 OpenAI：

```json
{
  "object": "list",
  "data": [
    {
      "id": "langgraph-agent",
      "object": "model",
      "owned_by": "internal"
    }
  ]
}
```

### 4. 聊天请求转发

文件：[backend/open_webui/main.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/main.py:1635)

OpenWebUI 前端发起聊天后，后端入口是：

```python
POST /api/chat/completions
POST /api/v1/chat/completions
```

这个接口会：

1. 检查模型是否存在。
2. 检查用户是否有权限访问该模型。
3. 处理模型参数。
4. 将请求交给统一聊天调度逻辑。

调度文件：[backend/open_webui/utils/chat.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/utils/chat.py:158)

关键分发逻辑：

```python
if model.get('pipe'):
    return await generate_function_chat_completion(...)
if model.get('owned_by') == 'ollama':
    return await generate_ollama_chat_completion(...)
else:
    return await generate_openai_chat_completion(...)
```

对 LangGraph 最有用的是最后一种：

```python
return await generate_openai_chat_completion(...)
```

它会进入 OpenAI 路由转发。

### 5. OpenAI 兼容请求转发

文件：[backend/open_webui/routers/openai.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/routers/openai.py:1002)

OpenWebUI 会把请求转发到具体外部服务：

```python
request_url = f'{url}/chat/completions'
```

也就是说，如果你在 OpenWebUI 中配置：

```text
OPENAI_API_BASE_URL=http://langgraph-api:8000/v1
```

那么实际聊天请求会转发到：

```text
http://langgraph-api:8000/v1/chat/completions
```

因此 LangGraph 服务需要提供：

```http
POST /v1/chat/completions
```

请求体基本是 OpenAI Chat Completions 格式：

```json
{
  "model": "langgraph-agent",
  "messages": [
    {
      "role": "user",
      "content": "你好"
    }
  ],
  "stream": true
}
```

## 三、三种接入方案

## 方案 A：OpenAI 兼容适配层，推荐

这是最适合当前项目的方式。

架构：

```text
浏览器
  |
  | http://localhost:5173
  v
OpenWebUI 前端
  |
  | /api/chat/completions
  v
OpenWebUI 后端
  |
  | /v1/chat/completions
  v
LangGraph OpenAI 兼容适配服务
  |
  v
LangGraph 工作流
```

优点：

- 对 OpenWebUI 源码侵入最小。
- 可以保留 OpenWebUI 原有聊天界面。
- LangGraph 可以独立开发、独立测试、独立部署。
- 适配层可被其他 OpenAI 兼容客户端复用。
- 后续替换 LangGraph 实现，不影响 OpenWebUI 前端。

缺点：

- 需要自己写一个适配服务。
- 要把 LangGraph 的输入输出转换成 OpenAI Chat Completions 格式。
- 流式输出需要按 OpenAI SSE 格式封装。

适用场景：

- 你希望 OpenWebUI 只是内部用户入口。
- 你希望 LangGraph 承担真实业务智能体逻辑。
- 你希望以后可以接多个工作流或多个智能体。

## 方案 B：OpenWebUI Pipeline / Function 接入

OpenWebUI 有 Pipeline、Function、Tool 等扩展机制。

相关文件：

- [backend/open_webui/routers/pipelines.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/routers/pipelines.py:1)
- [backend/open_webui/routers/functions.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/routers/functions.py:1)
- [backend/open_webui/routers/tools.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/routers/tools.py:1)

这种方式可以让 OpenWebUI 在内部执行或调用自定义 Python 逻辑。

优点：

- 与 OpenWebUI 的扩展体系更贴近。
- 可以在模型前后做过滤、增强、拦截。
- 适合做小型增强逻辑。

缺点：

- OpenWebUI 的 Tools / Functions 会执行 Python 代码，权限和安全边界要非常谨慎。
- 工作流复杂后，调试与部署不如独立服务清晰。
- LangGraph 依赖、运行状态、业务配置会与 OpenWebUI 后端耦合。

适用场景：

- 只做少量消息预处理或后处理。
- 只为管理员或可信内部人员启用。
- 不希望单独维护 LangGraph 服务。

不建议把大型 LangGraph 业务工作流直接塞进 OpenWebUI Function 中。长期看，独立服务更清晰。

## 方案 C：直接修改 OpenWebUI 后端，深度集成 LangGraph

这种方式是在 OpenWebUI 后端中新增 LangGraph 路由或直接改聊天调度逻辑。

可能改动点：

- [backend/open_webui/main.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/main.py:1635)
- [backend/open_webui/utils/chat.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/utils/chat.py:158)
- [backend/open_webui/routers/openai.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/routers/openai.py:1002)

优点：

- 可以最大程度控制 OpenWebUI 与 LangGraph 的交互。
- 可以把 OpenWebUI 用户、权限、文件、RAG、会话元数据直接传给 LangGraph。

缺点：

- 源码侵入大。
- 后续升级 OpenWebUI 容易冲突。
- 调试复杂。
- 不利于把 LangGraph 工作流复用给其他系统。

只有在以下情况才建议考虑：

- 你已经非常熟悉 OpenWebUI 后端。
- 你需要深度改造会话、权限、文件、RAG、工具调用链。
- 你能接受维护一个长期分叉版本。

## 四、推荐架构：LangGraph OpenAI 兼容适配服务

## 1. 服务需要实现哪些接口

最小接口：

```http
GET /health
GET /v1/models
POST /v1/chat/completions
```

可选接口：

```http
POST /v1/embeddings
GET /v1/models/{model}
```

对 OpenWebUI 来说，先实现 `GET /v1/models` 和 `POST /v1/chat/completions` 即可完成基本聊天。

## 2. 非流式响应格式

OpenWebUI 期望外部服务返回 OpenAI 类似格式：

```json
{
  "id": "chatcmpl-langgraph-xxx",
  "object": "chat.completion",
  "created": 1710000000,
  "model": "langgraph-agent",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "这是 LangGraph 返回的内容"
      },
      "finish_reason": "stop"
    }
  ]
}
```

## 3. 流式响应格式

如果请求中包含：

```json
{
  "stream": true
}
```

建议返回 SSE：

```text
data: {"id":"chatcmpl-langgraph-xxx","object":"chat.completion.chunk","model":"langgraph-agent","choices":[{"index":0,"delta":{"content":"你"},"finish_reason":null}]}

data: {"id":"chatcmpl-langgraph-xxx","object":"chat.completion.chunk","model":"langgraph-agent","choices":[{"index":0,"delta":{"content":"好"},"finish_reason":null}]}

data: {"id":"chatcmpl-langgraph-xxx","object":"chat.completion.chunk","model":"langgraph-agent","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

OpenWebUI 后端在 [backend/open_webui/routers/openai.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/routers/openai.py:1180) 中会识别 `text/event-stream`，然后把流继续转给前端。

## 五、最小 LangGraph 适配服务示例

下面示例只用于理解结构，实际项目中应拆分为 `graph.py`、`server.py`、`schemas.py`、`config.py`。

```python
import json
import time
import uuid
from typing import Annotated, TypedDict

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.graph import StateGraph, START, END, add_messages


API_KEY = "dev-secret"
MODEL_ID = "langgraph-agent"


class State(TypedDict):
    messages: Annotated[list, add_messages]


def chatbot(state: State):
    last_message = state["messages"][-1]
    content = last_message.get("content", "") if isinstance(last_message, dict) else str(last_message)
    return {
        "messages": [
            {
                "role": "assistant",
                "content": f"LangGraph 已收到：{content}",
            }
        ]
    }


builder = StateGraph(State)
builder.add_node("chatbot", chatbot)
builder.add_edge(START, "chatbot")
builder.add_edge("chatbot", END)
graph = builder.compile()

app = FastAPI()


def check_auth(authorization: str | None):
    if not API_KEY:
        return

    expected = f"Bearer {API_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/v1/models")
def models(authorization: str | None = Header(default=None)):
    check_auth(authorization)
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_ID,
                "object": "model",
                "owned_by": "internal",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(payload: dict, authorization: str | None = Header(default=None)):
    check_auth(authorization)

    model = payload.get("model", MODEL_ID)
    messages = payload.get("messages", [])
    stream = payload.get("stream", False)
    request_id = f"chatcmpl-{uuid.uuid4().hex}"

    if model != MODEL_ID:
        raise HTTPException(status_code=404, detail="Model not found")

    result = graph.invoke({"messages": messages})
    answer = result["messages"][-1].content if hasattr(result["messages"][-1], "content") else result["messages"][-1]["content"]

    if not stream:
        return {
            "id": request_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": answer,
                    },
                    "finish_reason": "stop",
                }
            ],
        }

    async def event_stream():
        for char in answer:
            chunk = {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "content": char,
                        },
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

        done = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        }
        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

启动方式示例：

```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```

在宿主机验证：

```bash
curl http://localhost:8000/health
curl -H "Authorization: Bearer dev-secret" http://localhost:8000/v1/models
```

非流式聊天验证：

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "langgraph-agent",
    "messages": [
      {
        "role": "user",
        "content": "你好"
      }
    ],
    "stream": false
  }'
```

流式聊天验证：

```bash
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "langgraph-agent",
    "messages": [
      {
        "role": "user",
        "content": "请流式输出一句话"
      }
    ],
    "stream": true
  }'
```

## 六、在 OpenWebUI 中配置 LangGraph 服务

## 方式 1：通过环境变量配置

如果 LangGraph 服务运行在宿主机，OpenWebUI 在 Docker 容器中运行，Linux 下通常不能直接用 `localhost` 访问宿主机服务。

可以使用以下方式之一：

- 把 LangGraph 服务也放进同一个 Docker 网络。
- 使用宿主机实际 IP。
- 配置 `host.docker.internal`，并确保 Docker 环境支持。

示例：

```bash
OPENAI_API_BASE_URL=http://langgraph-api:8000/v1
OPENAI_API_KEY=dev-secret
ENABLE_OPENAI_API=True
```

如果有多个 OpenAI 兼容服务：

```bash
OPENAI_API_BASE_URLS=http://langgraph-api:8000/v1;https://api.openai.com/v1
OPENAI_API_KEYS=dev-secret;sk-xxxx
```

注意：

- `OPENAI_API_BASE_URLS` 与 `OPENAI_API_KEYS` 用英文分号 `;` 分隔。
- 两者数量要一致。
- OpenWebUI 会把第一个 URL 的第一个 Key 对应起来。

## 方式 2：通过管理员界面配置

如果你保留了管理员设置页面，可以在管理员界面中添加 OpenAI 兼容连接。

大致路径：

```text
管理员设置
  -> 连接
  -> OpenAI API
  -> 添加 Base URL 和 API Key
```

填写：

```text
Base URL: http://langgraph-api:8000/v1
API Key: dev-secret
```

保存后，OpenWebUI 会请求：

```text
http://langgraph-api:8000/v1/models
```

如果模型列表能返回 `langgraph-agent`，聊天页中就应该可以选择该模型。

## 七、Docker 开发环境中的推荐连接方式

你当前已有开发容器：

```text
镜像：my-openwebui-dev:0.1
容器：openwebui-dev
```

推荐把 LangGraph 服务也放入 Docker，和 `openwebui-dev` 加入同一个网络。

示例：

```bash
docker network create openwebui-net
docker network connect openwebui-net openwebui-dev
docker run -d --name langgraph-api \
  --network openwebui-net \
  -p 8000:8000 \
  your-langgraph-image:0.1
```

此时 OpenWebUI 容器访问 LangGraph 服务应使用：

```text
http://langgraph-api:8000/v1
```

不要在 OpenWebUI 容器里写：

```text
http://localhost:8000/v1
```

原因：

- 在容器内部，`localhost` 指的是当前容器自己。
- `openwebui-dev` 容器里的 `localhost:8000` 不是 `langgraph-api` 容器。
- 容器之间应通过 Docker 网络和容器名通信。

验证命令：

```bash
docker exec -it openwebui-dev bash
curl http://langgraph-api:8000/health
curl -H "Authorization: Bearer dev-secret" http://langgraph-api:8000/v1/models
```

## 八、OpenWebUI 与 LangGraph 的数据边界

## 1. OpenWebUI 发给 LangGraph 的主要数据

OpenWebUI 最终会向 LangGraph 适配服务发送 OpenAI Chat Completions 风格请求：

```json
{
  "model": "langgraph-agent",
  "messages": [],
  "stream": true,
  "temperature": 0.7,
  "max_tokens": 1024
}
```

其中最重要的是：

- `model`：当前选择的模型 ID。
- `messages`：历史消息。
- `stream`：是否流式输出。
- `temperature`、`max_tokens` 等：模型参数。

如果你需要用户信息，可以研究 OpenWebUI 的请求头转发配置：

文件：[backend/open_webui/routers/openai.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/routers/openai.py:134)

相关逻辑：

```python
if ENABLE_FORWARD_USER_INFO_HEADERS and user:
    headers = include_user_info_headers(headers, user)
```

这意味着可以通过配置让 OpenWebUI 把用户信息放到请求头中，再由 LangGraph 服务读取。

## 2. LangGraph 返回给 OpenWebUI 的主要数据

基础聊天只需要返回：

- assistant 消息内容
- finish_reason
- 可选 usage

如果做高级能力，可逐步增加：

- 工具调用结果
- 引用来源
- token 使用量
- 中间状态提示

建议一开始只实现文本聊天，确认链路跑通后再扩展。

## 九、与 LangGraph 官方能力的关系

LangGraph 本身提供的是图编排能力。官方文档中，`StateGraph` 是构建图的核心，编译后图对象支持 `invoke()`、`stream()`、`ainvoke()`、`astream()` 等执行方式。

对 OpenWebUI 接入来说，重点不是把 LangGraph 的所有 API 暴露出来，而是把 LangGraph 的一次执行包装成 OpenAI 兼容的聊天响应。

建议理解顺序：

1. 先理解 LangGraph 的 `StateGraph`、`START`、`END`、`add_messages`。
2. 再理解 `graph.invoke()` 如何从输入消息得到最终状态。
3. 再理解 `graph.stream()` 或 `graph.astream()` 如何产生中间输出。
4. 最后再把这些输出转换成 OpenAI Chat Completions 或 SSE。

参考资料：

- LangGraph 图 API：<https://docs.langchain.com/oss/python/langgraph/use-graph-api>
- LangGraph Streaming：<https://docs.langchain.com/oss/python/langgraph/streaming>
- LangGraph Python Reference：<https://reference.langchain.com/python/langgraph/graph>
- LangGraph Server API：<https://langchain-ai.lang.chat/langgraph/cloud/reference/api/api_ref/>

## 十、开发验证顺序

建议按下面顺序推进，不要一开始就做复杂智能体。

## 第一步：验证 LangGraph 服务自己可用

目标：

- `GET /health` 正常。
- `GET /v1/models` 正常。
- `POST /v1/chat/completions` 非流式正常。
- `POST /v1/chat/completions` 流式正常。

只用 `curl` 验证，不接 OpenWebUI。

## 第二步：验证 OpenWebUI 能看到模型

目标：

- 在 OpenWebUI 管理员连接配置中添加 LangGraph Base URL。
- 聊天页模型选择器能看到 `langgraph-agent`。

如果看不到模型，优先检查：

- OpenWebUI 容器能否访问 LangGraph 容器。
- `/v1/models` 是否返回正确 JSON。
- API Key 是否匹配。
- `OPENAI_API_BASE_URLS` 是否包含 `/v1`。

## 第三步：验证非流式聊天

目标：

- 在 OpenWebUI 选择 `langgraph-agent`。
- 发送一句简单问题。
- 页面能显示 LangGraph 返回结果。

如果失败，优先检查：

- LangGraph 服务日志。
- OpenWebUI 后端日志。
- 请求是否打到了 `/v1/chat/completions`。
- 返回 JSON 是否符合 OpenAI Chat Completions 格式。

## 第四步：验证流式聊天

目标：

- OpenWebUI 页面逐字或逐段显示。
- LangGraph 服务返回 `Content-Type: text/event-stream`。
- SSE 每段以 `data: ...\n\n` 输出。
- 最后输出 `data: [DONE]\n\n`。

如果非流式正常、流式失败，问题通常在 SSE 格式。

## 第五步：再接入真实业务图

目标：

- 把示例 `chatbot` 节点替换成真实 LangGraph 工作流。
- 明确每个节点的输入、输出、错误处理。
- 增加日志和 trace id。
- 再考虑工具调用、RAG、数据库、外部 API。

## 十一、常见问题

## 1. 为什么不建议前端直接调用 LangGraph？

原因：

- 前端会暴露 LangGraph 服务地址和认证信息。
- OpenWebUI 的会话、权限、文件、用户上下文都在后端。
- 前端直接调用会绕过 OpenWebUI 后端的统一管理。
- 后续维护会变成两套调用链。

正确方式是：

```text
前端 -> OpenWebUI 后端 -> LangGraph 适配服务
```

## 2. Base URL 应该填 `/v1` 还是不填？

应该填到 OpenAI 兼容 API 的版本前缀。

如果你的 LangGraph 适配服务接口是：

```text
http://langgraph-api:8000/v1/models
http://langgraph-api:8000/v1/chat/completions
```

那么 OpenWebUI 中应配置：

```text
http://langgraph-api:8000/v1
```

不要配置成：

```text
http://langgraph-api:8000
```

否则 OpenWebUI 会请求：

```text
http://langgraph-api:8000/models
http://langgraph-api:8000/chat/completions
```

这会导致接口路径不匹配。

## 3. LangGraph 服务是否必须叫 OpenAI？

不是。

这里的 “OpenAI 兼容” 只表示接口格式兼容，不表示一定使用 OpenAI 官方服务。

OpenWebUI 的 OpenAI 路由本质是一个兼容协议代理：

```text
OpenWebUI -> 任何实现 /models 与 /chat/completions 的服务
```

LangGraph 只是其中一种后端实现。

## 4. 如何传递当前登录用户信息？

可以考虑两种方式：

1. 使用 OpenWebUI 的用户信息请求头转发能力。
2. 在 OpenWebUI 源码中扩展 metadata，再转发给外部服务。

建议先使用请求头方式，侵入更小。

需要关注：

- `ENABLE_FORWARD_USER_INFO_HEADERS`
- `include_user_info_headers`
- [backend/open_webui/routers/openai.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/routers/openai.py:134)

## 5. 如何把多个 LangGraph 工作流显示成多个模型？

让 `/v1/models` 返回多个模型：

```json
{
  "object": "list",
  "data": [
    {
      "id": "marketing-agent",
      "object": "model",
      "owned_by": "internal"
    },
    {
      "id": "report-agent",
      "object": "model",
      "owned_by": "internal"
    }
  ]
}
```

然后在 `/v1/chat/completions` 中根据 `payload["model"]` 选择不同 graph：

```python
if model == "marketing-agent":
    result = marketing_graph.invoke(...)
elif model == "report-agent":
    result = report_graph.invoke(...)
else:
    raise HTTPException(status_code=404, detail="Model not found")
```

这样 OpenWebUI 中会看到多个模型，但背后都是你的 LangGraph 工作流。

## 6. 如何处理文件上传？

OpenWebUI 的文件上传、知识库、RAG 有自己的体系。初期不建议直接让 LangGraph 处理文件上传。

建议分阶段：

1. 第一阶段：只接普通文本聊天。
2. 第二阶段：让 OpenWebUI 处理文件和知识库，LangGraph 只接收整理后的 messages。
3. 第三阶段：如果业务必须由 LangGraph 处理文件，再研究 OpenWebUI 的文件接口和 metadata 传递。

相关目录：

- [backend/open_webui/routers/files.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/routers/files.py:1)
- [backend/open_webui/routers/retrieval.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/routers/retrieval.py:1)
- [src/lib/components/chat/MessageInput](/home/zhongjinyan/project/program/open-webui/src/lib/components/chat/MessageInput)

## 十二、建议的学习路线

## 第一阶段：先学 OpenWebUI 的主链路

重点文件：

- [src/lib/constants.ts](/home/zhongjinyan/project/program/open-webui/src/lib/constants.ts:1)
- [src/routes/+layout.svelte](/home/zhongjinyan/project/program/open-webui/src/routes/+layout.svelte:1)
- [backend/start.sh](/home/zhongjinyan/project/program/open-webui/backend/start.sh:1)
- [backend/open_webui/main.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/main.py:1)
- [backend/open_webui/utils/chat.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/utils/chat.py:1)

需要搞清楚：

- 前端如何知道后端地址。
- 页面初始化如何拿后端配置。
- 聊天请求从哪个接口进入。
- 模型列表从哪里加载。
- 聊天结果如何流式返回给前端。

## 第二阶段：再学 OpenAI 兼容代理

重点文件：

- [backend/open_webui/config.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/config.py:1046)
- [backend/open_webui/routers/openai.py](/home/zhongjinyan/project/program/open-webui/backend/open_webui/routers/openai.py:1)
- [src/lib/apis/openai/index.ts](/home/zhongjinyan/project/program/open-webui/src/lib/apis/openai/index.ts:1)

需要搞清楚：

- OpenWebUI 如何保存 OpenAI Base URL。
- 如何拉取外部模型列表。
- 如何根据模型找到对应外部服务。
- 如何转发 `/chat/completions`。
- 流式响应如何透传。

## 第三阶段：再学 LangGraph

先不要从复杂 Agent 开始，先掌握：

- `StateGraph`
- `MessagesState`
- `add_messages`
- `graph.invoke`
- `graph.stream`
- 节点输入输出
- 状态合并
- 错误处理

等你能用 `curl` 调通一个最小 LangGraph OpenAI 兼容服务后，再逐步加入：

- 工具调用
- RAG
- 数据库读写
- 多节点条件分支
- 人工确认节点
- 长期记忆

## 第四阶段：再做内部产品化

当链路跑通后，再考虑：

- 品牌替换
- 用户界面精简
- 隐藏高级设置
- 固定默认模型
- 限制普通用户模型选择
- 关闭用户自定义工具能力
- 只保留内部业务需要的页面

不要在接入 LangGraph 前就大规模改 UI。否则一旦聊天链路出问题，很难判断是 UI 改动、OpenWebUI 配置、Docker 网络还是 LangGraph 服务的问题。

## 十三、推荐实施顺序

建议按这个顺序做：

1. 新建独立 LangGraph 服务目录，例如 `/home/zhongjinyan/project/program/langgraph-service`。
2. 写最小 `GET /health`、`GET /v1/models`、`POST /v1/chat/completions`。
3. 用 `curl` 验证 LangGraph 服务。
4. 将 LangGraph 服务放入 Docker，并加入 OpenWebUI 同一网络。
5. 在 OpenWebUI 管理员界面或环境变量中配置 `OPENAI_API_BASE_URL`。
6. 在 OpenWebUI 页面确认能看到 `langgraph-agent`。
7. 先验证非流式聊天。
8. 再验证流式聊天。
9. 替换为真实 LangGraph 工作流。
10. 最后再做品牌清理和界面精简。

这个顺序的核心原则是：

- 先跑通后端协议。
- 再接入 OpenWebUI。
- 再接业务逻辑。
- 最后改界面。

