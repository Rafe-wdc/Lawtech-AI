# Drafting templates: layout extraction report

Generated 2026-10-06 by running `core.template_layout.template_to_artifact`
over every document in the `drafting` Elasticsearch index.

## Corpus

- 498 templates, all `.csv` text extracts of court formats
  (`/content/formats_and_all_drafts/*.csv`).
- Layout survived only as whitespace: tab or space indentation for centred
  lines, a run of tabs before the party label ("Address: ___<tabs>___ Applicant").
- Paragraphs are hard-wrapped at about 80-120 characters.
- 318 templates had encoding damage (UTF-8 quotes, dashes and the rupee sign
  read as Latin-1: `Honâ€™ble`). All of it is repaired exactly.

## Result

| | Templates |
|---|---|
| Parsed into blocks without error | 498 / 498 |
| Centred title or heading found | 489 (98%) |
| Party or signature rows (label on the right) | 358 (71%) |
| Numbered paragraphs | 475 (95%) |
| Signature block | 243 (49%) |
| No layout hints at all | 8 |
| Mostly plain text (>60% unnumbered blocks) | 120, mainly deeds (35), agreements (23), wills, leases, bonds, powers of attorney: prose by nature |

Checked visually on the bail application (High Court), written statement
(maintenance), Section 138 notice and writ petition (prohibition) templates.

## Corpus problems to fix at the source

These two templates do not contain the document their name says. If the
drafting picker selects one, the writer receives junk as its reference.

| File | Actual content |
|---|---|
| `Format of Notice for Notice by Landlord to Tenant for Eviction.csv` | Notepad++ v8.7.7 release notes |
| `Notice To A Lady With Close Acquaintance Who Is Subjecting Our Client To Physica….csv` | CrPC / BNSS section-by-section comparison text |

## Appended documents lists

378 templates end with a "List of Required Documents" / "LIST OF DOCUMENTS"
block. It is kept as a separate final section ("Documents Required" when the
heading reads as guidance, "List Of Documents" otherwise) so the drafting
step can decide whether it belongs in the draft, and the signature block is
recognised before it.
