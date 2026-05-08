export const VISIT_PREPARATION_SHEET_FIELDS = [
	'客户单位',
	'所属行业/细分行业',
	'是否预约成功',
	'合作项目',
	'客户及职务',
	'客户背景',
	'认知期望',
	'拜访目标',
	'预约理由',
	'开场暖场',
	'了解最新变化',
	'了解认知期望',
	'呈现差异优势',
	'获得行动承诺',
	'处理客户顾虑',
	'总结确认'
] as const;

export type VisitPreparationSheetField = (typeof VISIT_PREPARATION_SHEET_FIELDS)[number];

export const createVisitPreparationSheetTemplate = (): Record<VisitPreparationSheetField, string> =>
	Object.fromEntries(
		VISIT_PREPARATION_SHEET_FIELDS.map((field) => [field, ''])
	) as Record<VisitPreparationSheetField, string>;
