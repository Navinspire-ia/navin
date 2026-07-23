# Document skills

Skills preloaded by `/studio` and related helpers available to the agent.

## Preloaded by `/studio`

| Skill | Purpose |
| --- | --- |
| `pptx-generator` | Builds PowerPoint files with python-pptx: layouts, themes, charts, speaker notes. |
| `docx-generator` | Builds Word documents with python-docx: styles, headings, tables, headers/footers. |
| `pdf-generator` | Produces PDF reports and one-pagers with consistent typography. |
| `spreadsheet-analyst` | Builds and analyzes Excel workbooks with openpyxl: formulas, conditional formatting, dashboards. |
| `presentation-designer` | Slide design principles: narrative arc, visual hierarchy, one idea per slide. |
| `professional-writer` | Business writing: clarity, tone, structure. |
| `document-templates` | 8 built-in visual themes (exact palettes, fonts, layout rules) + adapting user-provided template files. |

## Complementary skills

| Skill | Purpose |
| --- | --- |
| `report-generator` | Recurring structured reports. |
| `template-manager` | Reusing and adapting document templates. |
| `proposal-writer` / `sales-proposal-writer` / `rfp-writer` | Specialized proposal and tender writing. |
| `case-study-writer` | Customer case studies. |
| `technical-writer` | Technical documentation style. |
| `proofreader` / `style-editor` | Quality pass on the final text. |
| `translation-localization` | Producing documents in multiple languages. |
| `pdf-ocr-extractor` | Extracting content from existing PDFs to reuse. |
| `invoice-reader` / `contract-extractor` | Structured extraction from business documents. |
| `fact-checker` | Verifying claims before they land in a deliverable. |
| `image-generation` | Illustrations and cover images for decks and reports. |

Skills load automatically with `/studio`; you can also invoke any of them explicitly ("use the template-manager skill to adapt last month's report").
