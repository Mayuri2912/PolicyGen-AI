# Customer document generation

The bundled Sapres IMS Word template is the format authority. The renderer opens
that reference package, reuses its cover, change-history table, styles, page
geometry and ownership footer layout, and replaces sample document titles and
contents with the selected document's details. Word contents and page fields
refresh when opened. The PDF renderer follows the same document structure and
brand components; the configured organization owns the document. The Sapres logo is used only for Sapres; other organizations receive their company name in its place. Fonts depend on the fonts available on the server.

Customer Word/PDF outputs contain no AI generation notice, model/provider
attribution, generation instructions or appendices. Application review metadata,
source references, findings and documented/proposed distinctions remain in the
review tools. Normal draft/approval status and unknown-information placeholders
remain; removal of generation commentary does not imply approval.

Objectives and Principles are one section. Legacy separate sections are merged
for export and new revisions without changing the stored version. Removed
appendices cannot be enabled through legacy configuration flags. Export cache
stamps invalidate documents rendered with the earlier format.

The customer catalogue contains 45 distinct IDs from the supplied workbook:
I-01 through I-15, Q-01 through Q-10, S-01 through S-15 and EU-01 through EU-05.
Each entry has its exact title, management-system scope, document kind, subject
topics and operational guidance. The workbook's second list repeats existing
IDs; these are not separate documents. Existing 15 policy types remain available.

Specialist drafts receive evidence selected by subject/category and linked
requirements, risks and controls. General assessment summaries are excluded.
Quality checks flag out-of-scope finding references for correction. Vendor
documents cover the supplier lifecycle, due diligence, contracts, vendor access
and information handling, ongoing assurance, incident coordination and exit.
Missing evidence produces proposed requirements or placeholders, not assertions
that a vendor has a weakness. AI recruitment governance content remains valid
substantive content in EU-02.

Local verification includes catalogue coverage, structured drafts for all 45
entries, mixed vendor/ISMS evidence, legacy section merging, export contents,
template package features and PDF layout checks. Word layout verification
requires Word or LibreOffice; the local packaged renderer reported that
LibreOffice is not installed.

Model blocks that mix paragraph labels with list, statement or table payloads are split into their appropriate renderable types. This also applies to previously saved versions on export. Objectives containing only introductory text and labels are flagged for correction.

BCDR, Business Continuity, Configuration Management and the related catalogue plan/procedure explicitly cover customer-managed projects: contracts, shared responsibilities, customer-approved plans/baselines, authorization boundaries, project delivery continuity, recovery/change coordination, incident notifications, exceptions and permitted compliance evidence. Relevant customer-project facts are retained during subject filtering. Empty subsections and missing customer-project coverage are eligible for bounded quality correction. Unknown customer obligations remain placeholders.
