export const VISIT_PREPARATION_SHEET_FIELDS = [
	'基础信息-客户单位',
	'基础信息-所属行业/细分行业',
	'基础信息-是否预约成功',
	'基础信息-合作项目',
	'基础信息-客户及职务',
	'基础信息-客户背景',
	'认知期望',
	'拜访目标',
	'预约理由',
	'沟通过程-开场暖场',
	'沟通过程-了解最新变化',
	'沟通过程-了解认知期望',
	'沟通过程-呈现差异优势',
	'后续计划-获得行动承诺',
	'后续计划-处理客户顾虑',
	'总结确认'
] as const;

export type VisitPreparationSheetField = (typeof VISIT_PREPARATION_SHEET_FIELDS)[number];

export const createVisitPreparationSheetTemplate = (): Record<VisitPreparationSheetField, string> =>
	Object.fromEntries(
		VISIT_PREPARATION_SHEET_FIELDS.map((field) => [field, ''])
	) as Record<VisitPreparationSheetField, string>;
