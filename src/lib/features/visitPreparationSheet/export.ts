import JSZip from 'jszip';

import type { VisitPreparationSheetData } from './parse';
import type { VisitPreparationSheetField } from './schema';

const VISIT_PREPARATION_SHEET_TEMPLATE_PATH = '/templates/visit-preparation-sheet-template.xlsx';

const SPREADSHEET_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main';
const OFFICE_REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
const PACKAGE_REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships';
const XML_NS = 'http://www.w3.org/XML/1998/namespace';

const VISIT_PREPARATION_SHEET_CELL_MAP: Record<VisitPreparationSheetField, string> = {
	'基础信息-客户单位': 'D2',
	'基础信息-所属行业/细分行业': 'K2',
	'基础信息-是否预约成功': 'N2',
	'基础信息-合作项目': 'D3',
	'基础信息-客户及职务': 'K3',
	'基础信息-客户背景': 'D4',
	'认知期望': 'D6',
	'拜访目标': 'D7',
	'预约理由': 'D8',
	'沟通过程-开场暖场': 'F9',
	'沟通过程-了解最新变化': 'F10',
	'沟通过程-了解认知期望': 'F11',
	'沟通过程-呈现差异优势': 'F12',
	'后续计划-获得行动承诺': 'F13',
	'后续计划-处理客户顾虑': 'F14',
	'总结确认': 'D15'
};

const XLSX_MIME_TYPE =
	'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

const fetchVisitPreparationSheetTemplate = async (): Promise<ArrayBuffer> => {
	const response = await fetch(VISIT_PREPARATION_SHEET_TEMPLATE_PATH);
	if (!response.ok) {
		throw new Error(`Failed to fetch template: ${response.status}`);
	}

	return await response.arrayBuffer();
};

const parseXml = (content: string): XMLDocument => new DOMParser().parseFromString(content, 'text/xml');

const serializeXml = (document: XMLDocument, source: string): string => {
	const serialized = new XMLSerializer().serializeToString(document).trimStart();
	if (serialized.startsWith('<?xml')) {
		return serialized;
	}

	const declaration = source.match(/^\s*<\?xml[^>]+\?>/)?.[0] ?? '';
	return declaration ? `${declaration}\n${serialized}` : serialized;
};

const getFirstWorksheetPath = async (zip: JSZip): Promise<string> => {
	const workbookFile = zip.file('xl/workbook.xml');
	const relsFile = zip.file('xl/_rels/workbook.xml.rels');

	if (!workbookFile || !relsFile) {
		throw new Error('Visit preparation sheet template is missing workbook metadata');
	}

	const workbookDocument = parseXml(await workbookFile.async('string'));
	const relationshipDocument = parseXml(await relsFile.async('string'));

	const firstSheet = workbookDocument.getElementsByTagNameNS(SPREADSHEET_NS, 'sheet')[0];
	const relationshipId =
		firstSheet?.getAttributeNS(OFFICE_REL_NS, 'id') ?? firstSheet?.getAttribute('r:id');

	if (!relationshipId) {
		throw new Error('Visit preparation sheet template has no worksheet relationship');
	}

	const relationships = Array.from(
		relationshipDocument.getElementsByTagNameNS(PACKAGE_REL_NS, 'Relationship')
	);
	const relationship = relationships.find((item) => item.getAttribute('Id') === relationshipId);
	const target = relationship?.getAttribute('Target');

	if (!target) {
		throw new Error('Visit preparation sheet template worksheet target is missing');
	}

	return target.startsWith('/') ? target.slice(1) : `xl/${target.replace(/^\.\//, '')}`;
};

const getWorksheetCell = (document: XMLDocument, address: string): Element | null =>
	Array.from(document.getElementsByTagNameNS(SPREADSHEET_NS, 'c')).find(
		(cell) => cell.getAttribute('r') === address
	) ?? null;

const clearWorksheetCellValue = (cell: Element) => {
	cell.removeAttribute('t');

	while (cell.firstChild) {
		cell.removeChild(cell.firstChild);
	}
};

const setWorksheetCellValue = (document: XMLDocument, address: string, value: string) => {
	const cell = getWorksheetCell(document, address);
	if (!cell) {
		throw new Error(`Visit preparation sheet template cell is missing: ${address}`);
	}

	clearWorksheetCellValue(cell);

	if (value === '') {
		return;
	}

	cell.setAttribute('t', 'inlineStr');

	const inlineString = document.createElementNS(SPREADSHEET_NS, 'is');
	const textNode = document.createElementNS(SPREADSHEET_NS, 't');
	textNode.textContent = value;

	if (/^\s|\s$/.test(value)) {
		textNode.setAttributeNS(XML_NS, 'xml:space', 'preserve');
	}

	inlineString.appendChild(textNode);
	cell.appendChild(inlineString);
};

const createFilledVisitPreparationSheetBlob = async (
	sheet: VisitPreparationSheetData
): Promise<Blob> => {
	const zip = await JSZip.loadAsync(await fetchVisitPreparationSheetTemplate());
	const worksheetPath = await getFirstWorksheetPath(zip);
	const worksheetFile = zip.file(worksheetPath);

	if (!worksheetFile) {
		throw new Error(`Visit preparation sheet worksheet file is missing: ${worksheetPath}`);
	}

	const worksheetSource = await worksheetFile.async('string');
	const worksheetDocument = parseXml(worksheetSource);

	for (const [field, address] of Object.entries(VISIT_PREPARATION_SHEET_CELL_MAP)) {
		setWorksheetCellValue(
			worksheetDocument,
			address,
			sheet[field as VisitPreparationSheetField] ?? ''
		);
	}

	zip.file(worksheetPath, serializeXml(worksheetDocument, worksheetSource));

	return await zip.generateAsync({
		type: 'blob',
		mimeType: XLSX_MIME_TYPE
	});
};

const downloadBlob = (blob: Blob, fileName: string) => {
	const url = URL.createObjectURL(blob);
	const anchor = document.createElement('a');

	anchor.href = url;
	anchor.download = fileName;
	document.body.appendChild(anchor);
	anchor.click();
	document.body.removeChild(anchor);
	URL.revokeObjectURL(url);
};

export const downloadVisitPreparationSheet = async (
	sheet: VisitPreparationSheetData,
	fileName = '拜访准备表.xlsx'
) => {
	downloadBlob(await createFilledVisitPreparationSheetBlob(sheet), fileName);
};
