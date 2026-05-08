import {
	VISIT_PREPARATION_SHEET_FIELDS,
	type VisitPreparationSheetField
} from './schema';

export type VisitPreparationSheetData = Record<VisitPreparationSheetField, string>;

const stripMarkdownFence = (content: string): string => {
	const trimmed = content.trim();

	if (trimmed.startsWith('```json')) {
		return trimmed.slice(7).replace(/```$/, '').trim();
	}

	if (trimmed.startsWith('```')) {
		return trimmed.slice(3).replace(/```$/, '').trim();
	}

	return trimmed;
};

const extractJsonObject = (content: string): string | null => {
	const normalized = stripMarkdownFence(content);
	const start = normalized.indexOf('{');
	const end = normalized.lastIndexOf('}');

	if (start === -1 || end === -1 || end < start) {
		return null;
	}

	return normalized.slice(start, end + 1);
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
	typeof value === 'object' && value !== null && !Array.isArray(value);

export const parseVisitPreparationSheet = (
	content: string
): VisitPreparationSheetData | null => {
	const jsonObject = extractJsonObject(content);
	if (!jsonObject) {
		return null;
	}

	try {
		const parsed = JSON.parse(jsonObject);
		if (!isRecord(parsed)) {
			return null;
		}

		const result = {} as VisitPreparationSheetData;
		for (const field of VISIT_PREPARATION_SHEET_FIELDS) {
			const value = parsed[field];
			if (typeof value !== 'string') {
				return null;
			}

			result[field] = value.trim();
		}

		return result;
	} catch {
		return null;
	}
};
