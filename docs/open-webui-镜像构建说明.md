# Open WebUI 镜像构建说明

本文档说明 [Dockerfile](/home/zhongjinyan/project/program/open-webui/Dockerfile) 如何构建 Open WebUI 镜像，并解释 [build-openwebui.sh](/home/zhongjinyan/project/program/build-openwebui.sh) 与镜像构建流程之间的关系。

## 一、结论概览

当前 Open WebUI 使用多阶段构建方式生成最终镜像：

1. 第一阶段使用 Node.js 镜像编译前端资源。
2. 第二阶段使用 Python 镜像安装后端依赖和运行环境。
3. 最终镜像以前端构建产物加后端运行环境组合而成。

需要注意的是：

- [Dockerfile](/home/zhongjinyan/project/program/open-webui/Dockerfile) 负责“构建镜像”。
- [build-openwebui.sh](/home/zhongjinyan/project/program/build-openwebui.sh) 当前只负责“启动容器”，并不执行 `docker build`。
- [docker/build-openwebui.sh](/home/zhongjinyan/project/docker/build-openwebui.sh) 与上面的脚本内容一致，同样不是构建脚本。

## 二、Dockerfile 的整体结构

`Dockerfile` 分为两个主要阶段：

### 1. 前端构建阶段 `build`

起点：

```dockerfile
FROM --platform=$BUILDPLATFORM node:22-alpine3.20 AS build
```

这一阶段的职责是把前端源码编译成静态资源，主要步骤如下：

1. 设置工作目录为 `/app`。
2. 安装 `git`，用于在构建过程中保留版本信息。
3. 复制 `package.json` 和 `package-lock.json`。
4. 执行 `npm ci --force` 安装前端依赖。
5. 复制整个项目源码。
6. 注入 `APP_BUILD_HASH`。
7. 执行 `npm run build`，生成前端打包结果。

这一阶段的主要产物是：

- `/app/build`
- `/app/CHANGELOG.md`
- `/app/package.json`

这些文件会在后续阶段被复制到最终镜像中。

### 2. 后端运行阶段 `base`

起点：

```dockerfile
FROM python:3.11.14-slim-bookworm AS base
```

这一阶段负责构建最终运行镜像，主要包括以下内容：

1. 接收构建参数，例如：
   - `USE_CUDA`
   - `USE_OLLAMA`
   - `USE_SLIM`
   - `USE_PERMISSION_HARDENING`
   - `USE_CUDA_VER`
   - `UID`
   - `GID`
2. 设置运行环境变量，例如端口、模型配置、缓存路径、API 配置等。
3. 根据 `UID` 和 `GID` 创建运行用户。
4. 初始化缓存目录，例如 Chroma 缓存目录。
5. 安装系统依赖，例如：
   - `git`
   - `build-essential`
   - `pandoc`
   - `curl`
   - `jq`
   - `python3-dev`
   - `ffmpeg`
6. 复制 `backend/requirements.txt` 并安装 Python 依赖。
7. 根据是否启用 CUDA，安装不同来源的 PyTorch。
8. 根据 `USE_SLIM` 决定是否预下载模型与语言资源。
9. 如果 `USE_OLLAMA=true`，额外安装 Ollama。
10. 从前端构建阶段复制前端产物。
11. 复制 `backend` 目录到最终镜像。
12. 配置健康检查、权限加固、默认用户和启动命令。

## 三、镜像是如何一步步生成的

从 Docker 的角度看，执行 `docker build` 时会按顺序处理 `Dockerfile` 中的每一条指令，每条指令都会形成镜像层。

### 第一步：读取构建参数

文件最开头定义了一组 `ARG`，用于控制构建行为，例如：

```dockerfile
ARG USE_CUDA=false
ARG USE_OLLAMA=false
ARG USE_SLIM=false
ARG USE_PERMISSION_HARDENING=false
ARG USE_CUDA_VER=cu128
```

这些参数决定了最终镜像中：

- 是否启用 CUDA 版 PyTorch
- 是否安装 Ollama
- 是否跳过部分模型预下载
- 是否启用 OpenShift 兼容的权限加固

### 第二步：构建前端

在 `build` 阶段中，Docker 会：

1. 拉取 `node:22-alpine3.20`
2. 安装前端依赖
3. 执行前端打包命令
4. 生成前端静态文件

这一阶段不会成为最终运行镜像，只是中间构建器。

### 第三步：准备 Python 运行环境

在 `base` 阶段中，Docker 会：

1. 拉取 `python:3.11.14-slim-bookworm`
2. 设置 `/app/backend` 为工作目录
3. 创建运行用户和相关目录
4. 安装系统级依赖

这一步的目标是准备一个适合运行后端服务的基础环境。

### 第四步：安装 Python 依赖和模型资源

`Dockerfile` 先复制：

```dockerfile
COPY --chown=$UID:$GID ./backend/requirements.txt ./requirements.txt
```

然后执行一个较长的 `RUN`，主要完成以下工作：

1. 安装 `uv`
2. 根据 `USE_CUDA` 选择 CUDA 版或 CPU 版 PyTorch
3. 安装 `requirements.txt` 中定义的 Python 依赖
4. 在非 `slim` 模式下预加载以下资源：
   - 主嵌入模型
   - 辅助嵌入模型
   - Whisper 模型
   - Tiktoken 编码
   - NLTK 数据

这样做的作用是：

- 减少容器首次启动时的在线下载行为
- 尽量把依赖固化到镜像里
- 提高首次使用体验

### 第五步：可选安装 Ollama

如果构建时传入：

```bash
--build-arg USE_OLLAMA=true
```

那么会执行：

```dockerfile
curl -fsSL https://ollama.com/install.sh | sh
```

也就是说，Ollama 是否进入最终镜像完全由构建参数决定。

### 第六步：把前端产物复制到最终镜像

后端阶段会从前端构建阶段复制以下文件：

```dockerfile
COPY --chown=$UID:$GID --from=build /app/build /app/build
COPY --chown=$UID:$GID --from=build /app/CHANGELOG.md /app/CHANGELOG.md
COPY --chown=$UID:$GID --from=build /app/package.json /app/package.json
```

这一步把前端打包结果并入最终镜像，因此最终镜像同时包含：

- 前端静态资源
- 后端 Python 服务

### 第七步：复制后端代码并形成最终镜像

最后会执行：

```dockerfile
COPY --chown=$UID:$GID ./backend .
```

然后配置：

- `EXPOSE 8080`
- `HEALTHCHECK`
- `USER $UID:$GID`
- `CMD [ "bash", "start.sh"]`

这意味着容器启动后，默认执行的是：

```bash
bash start.sh
```

## 四、最终镜像里包含什么

构建完成后的镜像大致包含以下内容：

- 基于 `python:3.11.14-slim-bookworm` 的运行环境
- Open WebUI 后端代码
- 前端打包后的静态文件
- Python 依赖
- 部分系统工具和库
- 可选的 CUDA 版 PyTorch
- 可选的 Ollama
- 可选的预下载模型和缓存资源

因此，最终镜像不是纯前端镜像，也不是纯后端镜像，而是一个可直接运行 Open WebUI 的完整应用镜像。

## 五、如何执行镜像构建

当前仓库中没有发现专门的 `docker build` 脚本，因此镜像通常需要手动构建。

建议在目录 [program/open-webui](/home/zhongjinyan/project/program/open-webui) 下执行：

```bash
docker build -t my-openwebui-internal:0.1 .
```

如果需要启用 CUDA、Ollama 或其他构建参数，可以这样构建：

```bash
docker build -t my-openwebui-internal:0.1 \
  --build-arg USE_CUDA=true \
  --build-arg USE_CUDA_VER=cu128 \
  --build-arg USE_OLLAMA=true \
  .
```

如果希望使用更小的镜像并跳过部分预下载资源，可以加入：

```bash
--build-arg USE_SLIM=true
```

## 六、build-openwebui.sh 实际做了什么

脚本 [program/build-openwebui.sh](/home/zhongjinyan/project/program/build-openwebui.sh) 的内容核心是：

```bash
sudo docker run -it --name "$NAME" \
  --runtime=nvidia \
  --ipc=host \
  --gpus all \
  -v ${CODE_PATH}:/workspace \
  -v ${DATA_PATH}:/data \
  "$IMAGE"
```

这说明它做的是“运行已有镜像”，而不是“构建新镜像”。

### 脚本行为说明

- 使用镜像 `my-openwebui-internal:0.1`
- 容器名为 `openweb`
- 挂载源码目录到容器内 `/workspace`
- 挂载数据目录 `/data`
- 启用 NVIDIA GPU
- 开启 `--ipc=host`

### 这意味着什么

这个脚本默认假设镜像已经提前构建好。如果本地不存在 `my-openwebui-internal:0.1`，执行该脚本会直接失败。

换句话说，正确顺序应该是：

1. 先执行 `docker build` 构建镜像。
2. 再执行 `build-openwebui.sh` 运行容器。

## 七、构建参数对镜像行为的影响

### `USE_CUDA`

- `true`：安装 CUDA 版 PyTorch
- `false`：安装 CPU 版 PyTorch

### `USE_CUDA_VER`

- 控制 PyTorch 使用哪个 CUDA wheel 源
- 当前默认值为 `cu128`

### `USE_OLLAMA`

- `true`：在镜像中安装 Ollama
- `false`：不安装

### `USE_SLIM`

- `true`：跳过部分模型预下载，减少镜像构建时长和体积
- `false`：预下载模型和部分资源，首次启动更完整

### `USE_PERMISSION_HARDENING`

- `true`：对 `/app` 和 `/root` 做 OpenShift 场景下的权限加固
- `false`：保持默认权限逻辑

### `UID` / `GID`

- 控制镜像中运行用户和用户组
- 默认是 `0:0`

## 八、推荐的理解方式

可以把这个流程理解为三层：

1. `Dockerfile` 负责定义“镜像怎么做出来”。
2. `docker build` 负责按照 `Dockerfile` 真正生成镜像。
3. `build-openwebui.sh` 负责基于现成镜像启动一个容器。

因此，`Dockerfile` 是构建规则，`docker build` 是执行构建，`docker run` 是启动实例，它们不是同一件事。

## 九、常见误区

### 误区 1：`build-openwebui.sh` 会构建镜像

不是。当前脚本只执行了 `docker run`，没有执行 `docker build`。

### 误区 2：前端和后端分别生成两个最终镜像

不是。前端阶段只是中间构建阶段，最终只会产出一个运行镜像。

### 误区 3：启用 CUDA 只影响运行，不影响构建

不是。`USE_CUDA` 会直接影响构建阶段安装的 PyTorch 包来源，因此它会改变最终镜像内容。

## 十、建议

如果后续希望流程更清晰，建议把当前的 `build-openwebui.sh` 重命名为类似：

- `run-openwebui.sh`

或者新增一个真正的构建脚本，例如：

- `docker-build-openwebui.sh`

这样可以把“构建镜像”和“运行容器”这两件事明确分开。
