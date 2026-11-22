## Task: write the policy

Write a complete **{{policy_name}}** for the organization described by the configuration and analysis below.

{{document_guidance}}

Build every section from findings that belong in a {{policy_name}}, including objectives, responsibilities, risks, controls and monitoring. Do not copy a general ISMS assessment into a specialist document. When a vendor risk is not evidenced, describe a proposed vendor assessment requirement rather than asserting that the vendor has that weakness.

### Configuration

{{configuration}}

Empty configuration values are unknown. Use the placeholder form for them wherever they are needed; do not make them up.

### Writing style and depth

{{style_guidance}}

{{detail_guidance}}

### Structure

Write these sections, in this order, using exactly these `id` and `heading` values. The guidance says what each section is for. Skip nothing; if the analysis offers little for a section, write what can honestly be said and use placeholders for the rest.

{{sections}}

The application adds the cover page, document identity, change history and contents itself. There are NO appendices. Do not add or refer to an appendix, annex or evidence mapping attachment. Put necessary requirements and relevant risk treatments in the main sections.

### Block types

Each section has a `blocks` list. Every block has all of these fields; fill the ones its `type` uses and leave the others empty (`""` or `[]`).

- `paragraph`: prose in `text`.
- `subheading`: a short heading in `text`, to group what follows.
- `bullets` / `numbered`: entries in `items`.
- `statements`: mandatory policy requirements in `statements`. Each has `text` (one clear, testable requirement using "must" or "must not"), `basis` ("documented" or "proposed") and `refs` (ids of the findings that support it). Leave `id` empty.
- `table`: `columns` plus `rows`, where each row has one cell per column. Use tables for abbreviations, definitions and roles and responsibilities.

Put the binding requirements of the policy in `statements` blocks, grouped under subheadings by topic. Use paragraphs for context and explanation only.

### Three rules for this document

- Do not write an Executive Summary or any other summary section. The sections listed above are the whole policy.
- The `abbreviations` section is one table with the columns `Abbreviation` and `Full Form / Description`. Write it last, after you know which abbreviations the other sections use, and list only those. If there are none, write one sentence saying so.
- The `roles_responsibilities` section is a table whose first column is `Role / Department`. No cell, bullet, paragraph or statement anywhere in the policy may contain the name of an individual person; use the role, department or team.

### Output schema

{{schema}}

### Analysis

<untrusted_analysis id="{{nonce}}">
{{analysis}}
</untrusted_analysis id="{{nonce}}">

Now produce the JSON object for the policy.
