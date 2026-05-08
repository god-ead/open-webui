<script lang="ts">
	import Checkbox from '$lib/components/common/Checkbox.svelte';

	export let sales: {
		visit_preparation_sheet?: boolean;
	} = {};
</script>

<div>
	<div class="flex w-full justify-between mb-1">
		<div class="self-center text-xs font-medium text-gray-500">销售能力</div>
	</div>

	<div class="flex items-center mt-2 flex-wrap">
		<div class="flex items-center gap-2 mr-3">
			<Checkbox
				state={sales.visit_preparation_sheet ? 'checked' : 'unchecked'}
				on:change={(e) => {
					sales.visit_preparation_sheet = e.detail === 'checked';
				}}
			/>

			<div class="py-0.5 text-sm">拜访准备表</div>
		</div>
	</div>
</div>
