## Task: write the policy

Write a complete **{{policy_name}}** for the organization described by the configuration and analysis below.

The analysis may cover more subjects than this policy does, especially when several documents were uploaded. Build the policy from the findings that belong in a {{policy_name}}. Findings on unrelated subjects should not be turned into requirements here; they stay available to the reader in the appendices, and you can mention in one sentence that they exist.

### Configuration

{{configuration}}

Empty configuration values are unknown. Use the placeholder form for them wherever they are needed; do not make them up.

### Writing style and depth

{{style_guidance}}

{{detail_guidance}}

### Structure

Write these sections, in this order, using exactly these `id` and `heading` values. The guidance says what each section is for. Skip nothing; if the analysis offers little for a section, write what can honestly be said and use placeholders for the rest.

{{sections}}

The application adds the cover page, document control, version history, table of contents and the appendices (risk register, control matrix, gap analysis, evidence mapping) itself, so do not write those. You may refer to them, for example "see the Risk Register appendix".

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
