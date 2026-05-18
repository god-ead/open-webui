<script lang="ts">
	import { LinkPreview } from 'bits-ui';
	import { decodeString } from '$lib/utils';
	import Source from './Source.svelte';

	export let id;
	export let token;
	export let sourceIds: string[] = [];
	export let onClick: Function = () => {};

	let containerElement;
	let openPreview = false;

	// Helper function to return only the domain from a URL
	function getDomain(url: string): string {
		const domain = url.replace('http://', '').replace('https://', '').split(/[/?#]/)[0];

		if (domain.startsWith('www.')) {
			return domain.slice(4);
		}
		return domain;
	}

	// Helper function to check if text is a URL and return the domain
	function formattedTitle(title: string): string {
		if (title.startsWith('http')) {
			return getDomain(title);
		}

		return title;
	}

	const getDisplayTitle = (title: string) => {
		if (!title) return 'N/A';
		if (title.length > 30) {
			return title.slice(0, 15) + '...' + title.slice(-10);
		}
		return title;
	};

	const getSourceTitle = (identifier: string | number) => {
		const id = typeof identifier === 'string' ? parseInt(identifier.split('#')[0]) : identifier;
		return sourceIds[id - 1];
	};

	const getVisibleSourceItems = (): { identifier: string | number; title: string }[] =>
		(token.citationIdentifiers ?? token.ids ?? [])
			.map((identifier: string | number) => ({ identifier, title: getSourceTitle(identifier) ?? 'N/A' }))
			.filter((item: { identifier: string | number; title: string }) => item.title !== 'N/A');
</script>

{#if sourceIds}
	{@const visibleSourceItems = getVisibleSourceItems()}
	{#if visibleSourceItems.length === 1}
		<Source
			id={visibleSourceItems[0].identifier}
			title={visibleSourceItems[0].title}
			{onClick}
		/>
	{:else if visibleSourceItems.length > 1}
		<LinkPreview.Root openDelay={0} bind:open={openPreview}>
			<LinkPreview.Trigger>
				<button
					aria-label={`${getDisplayTitle(formattedTitle(decodeString(visibleSourceItems[0].title)))} +${visibleSourceItems.length - 1} more sources`}
					class="text-[10px] w-fit translate-y-[2px] px-2 py-0.5 dark:bg-white/5 dark:text-white/80 dark:hover:text-white bg-gray-50 text-black/80 hover:text-black transition rounded-xl"
					on:click={() => {
						openPreview = !openPreview;
					}}
				>
					<span class="line-clamp-1">
						{getDisplayTitle(formattedTitle(decodeString(visibleSourceItems[0].title)))}
						<span class="dark:text-white/50 text-black/50">+{visibleSourceItems.length - 1}</span>
					</span>
				</button>
			</LinkPreview.Trigger>
			<LinkPreview.Portal>
				<LinkPreview.Content class="z-[999]" align="start" strategy="fixed" sideOffset={6}>
					<div class="bg-gray-50 dark:bg-gray-850 rounded-xl p-1 cursor-pointer">
						{#each visibleSourceItems as item}
							<div class="">
								<Source id={item.identifier} title={item.title} {onClick} />
							</div>
						{/each}
					</div>
				</LinkPreview.Content>
			</LinkPreview.Portal>
		</LinkPreview.Root>
	{/if}
{:else}
	<span>{token.raw}</span>
{/if}
