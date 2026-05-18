export const VISIT_PREPARATION_SHEET_SUBMISSION_TYPE = 'visit_preparation_sheet';

export type VisitPreparationSheetMessageMeta = {
	meta?: {
		visit_preparation_sheet?: {
			triggered_by_button?: boolean;
		};
	};
};

type VisitPreparationSheetModel = {
	info?: {
		meta?: Record<string, any>;
	};
	meta?: Record<string, any>;
};

export const createVisitPreparationSheetMessageMeta = () => ({
	meta: {
		visit_preparation_sheet: {
			triggered_by_button: true
		}
	}
});

export const isVisitPreparationSheetTriggeredMessage = (message: unknown): boolean =>
	Boolean(
		(message as VisitPreparationSheetMessageMeta | null | undefined)?.meta
			?.visit_preparation_sheet?.triggered_by_button
	);

export const isVisitPreparationSheetEnabledForModel = (
	model: VisitPreparationSheetModel | null | undefined
): boolean =>
	Boolean(
		model?.info?.meta?.sales?.visit_preparation_sheet ??
			model?.meta?.sales?.visit_preparation_sheet
	);
