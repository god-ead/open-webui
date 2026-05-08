<script lang="ts">
	import Modal from '$lib/components/common/Modal.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	import type { VisitPreparationSheetData } from '$lib/features/visitPreparationSheet/parse';

	export let show = false;
	export let sheet: VisitPreparationSheetData | null = null;

	const topFields = ['客户单位', '所属行业/细分行业', '是否预约成功'] as const;
	const summaryFields = ['合作项目', '客户及职务', '客户背景'] as const;
	const preparationFields = ['认知期望', '拜访目标', '预约理由'] as const;
	const communicationFields = [
		'开场暖场',
		'了解最新变化',
		'了解认知期望',
		'呈现差异优势'
	] as const;
	const followUpFields = ['获得行动承诺', '处理客户顾虑'] as const;
</script>

<Modal size="xl" bind:show>
	<div class="flex flex-col">
		<div class="flex items-center justify-between px-5 pt-4 pb-3 border-b border-gray-100 dark:border-gray-800">
			<div>
				<div class="text-lg font-semibold text-gray-900 dark:text-gray-100">拜访准备表</div>
				<div class="text-sm text-gray-500 dark:text-gray-400">按模板结构整理的前端预览</div>
			</div>

			<button
				type="button"
				aria-label="关闭拜访准备表"
				title="关闭"
				class="rounded-full p-1.5 text-gray-500 transition hover:bg-gray-100 hover:text-gray-800 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-100"
				on:click={() => {
					show = false;
				}}
			>
				<XMark className="size-5" />
			</button>
		</div>

		{#if sheet}
			<div class="max-h-[80vh] overflow-y-auto px-5 py-4 text-sm">
				<div class="space-y-6">
					<section class="space-y-3">
						<div class="text-xs font-semibold tracking-wide text-gray-500 dark:text-gray-400">
							基础信息
						</div>

						<div class="grid gap-3 md:grid-cols-3">
							{#each topFields as field}
								<div class="rounded-2xl border border-gray-100 bg-gray-50/70 p-3 dark:border-gray-800 dark:bg-gray-900/60">
									<div class="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">{field}</div>
									<div class="whitespace-pre-wrap text-gray-900 dark:text-gray-100">
										{sheet[field]}
									</div>
								</div>
							{/each}
						</div>

						<div class="grid gap-3 md:grid-cols-2">
							{#each summaryFields as field}
								<div class="rounded-2xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-950/40 {field === '客户背景' ? 'md:col-span-2' : ''}">
									<div class="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">{field}</div>
									<div class="whitespace-pre-wrap leading-6 text-gray-900 dark:text-gray-100">
										{sheet[field]}
									</div>
								</div>
							{/each}
						</div>
					</section>

					<section class="space-y-3">
						<div class="text-xs font-semibold tracking-wide text-gray-500 dark:text-gray-400">
							拜访准备
						</div>

						<div class="grid gap-3 md:grid-cols-3">
							{#each preparationFields as field}
								<div class="rounded-2xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-950/40">
									<div class="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">{field}</div>
									<div class="whitespace-pre-wrap leading-6 text-gray-900 dark:text-gray-100">
										{sheet[field]}
									</div>
								</div>
							{/each}
						</div>
					</section>

					<section class="space-y-3">
						<div class="text-xs font-semibold tracking-wide text-gray-500 dark:text-gray-400">
							沟通过程
						</div>

						<div class="grid gap-3 md:grid-cols-2">
							{#each communicationFields as field}
								<div class="rounded-2xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-950/40">
									<div class="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">{field}</div>
									<div class="whitespace-pre-wrap leading-6 text-gray-900 dark:text-gray-100">
										{sheet[field]}
									</div>
								</div>
							{/each}
						</div>
					</section>

					<section class="space-y-3">
						<div class="text-xs font-semibold tracking-wide text-gray-500 dark:text-gray-400">
							后续计划
						</div>

						<div class="grid gap-3 md:grid-cols-2">
							{#each followUpFields as field}
								<div class="rounded-2xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-950/40">
									<div class="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">{field}</div>
									<div class="whitespace-pre-wrap leading-6 text-gray-900 dark:text-gray-100">
										{sheet[field]}
									</div>
								</div>
							{/each}
						</div>
					</section>

					<section class="space-y-3">
						<div class="text-xs font-semibold tracking-wide text-gray-500 dark:text-gray-400">
							总结确认
						</div>

						<div class="rounded-2xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-950/40">
							<div class="whitespace-pre-wrap leading-6 text-gray-900 dark:text-gray-100">
								{sheet['总结确认']}
							</div>
						</div>
					</section>
				</div>
			</div>
		{/if}
	</div>
</Modal>
