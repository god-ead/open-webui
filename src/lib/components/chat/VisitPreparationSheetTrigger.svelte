<script lang="ts">
	import Tooltip from '../common/Tooltip.svelte';
	import DocumentCheck from '../icons/DocumentCheck.svelte';

	import { buildVisitPreparationSheetSubmitPrompt } from '$lib/features/visitPreparationSheet/prompt';
	import { VISIT_PREPARATION_SHEET_SUBMISSION_TYPE } from '$lib/features/visitPreparationSheet/state';

	export let draftPrompt = '';
	export let disabled = false;
	export let onSubmit: (
		payload: { prompt: string; submissionType: string }
	) => void = () => {};

	const handleClick = () => {
		if (disabled) {
			return;
		}

		onSubmit({
			prompt: buildVisitPreparationSheetSubmitPrompt(draftPrompt),
			submissionType: VISIT_PREPARATION_SHEET_SUBMISSION_TYPE
		});
	};
</script>

<Tooltip content="基于当前聊天记录生成结构化拜访准备表" placement="top">
	<button
		type="button"
		aria-label="生成拜访准备表"
		title="生成拜访准备表"
		class="flex items-center gap-1 rounded-full px-2.5 py-1.5 text-sm transition-colors duration-300 {disabled
			? 'cursor-not-allowed text-gray-400 dark:text-gray-500'
			: 'text-gray-600 hover:bg-gray-50 dark:text-gray-300 dark:hover:bg-gray-800'}"
		disabled={disabled}
		on:click|preventDefault={handleClick}
	>
		<DocumentCheck className="size-4" strokeWidth="1.75" />
		<span class="hidden sm:inline">拜访准备表</span>
	</button>
</Tooltip>
