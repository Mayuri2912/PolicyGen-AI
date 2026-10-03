## Task: revise sections of an existing policy draft

You are revising part of a **{{policy_name}}** draft. Rewrite only the sections listed under "Sections to revise" and return them with the same `id` and `heading` values. The rest of the draft stays as it is, so keep terminology, role names and numbers consistent with the outline below.

### Why these sections are being revised

{{reason}}

{{instruction_block}}

### Configuration

{{configuration}}

### Writing style and depth

{{style_guidance}}

{{detail_guidance}}

### Outline of the full draft

{{outline}}

### Sections to revise

Each entry gives the section's purpose and its current content.

{{sections}}

### Block types

Every block has the fields `type`, `text`, `items`, `statements`, `columns` and `rows`; fill the ones the type uses and leave the others empty. Types: `paragraph`, `subheading`, `bullets`, `numbered`, `statements` (each with `text`, `basis` of "documented" or "proposed", `refs` of supporting finding ids, and an empty `id`), and `table`.

### Rules that still apply

- Do not add an Executive Summary or any section that was not asked for.
- Do not name individual people. Use role titles, departments or teams, and `[To be confirmed: role title]` where the documents do not state the role. A requested change does not override this.
- If you revise `abbreviations`, return one table with the columns `Abbreviation` and `Full Form / Description`, listing only abbreviations the policy uses. If you revise `roles_responsibilities`, keep `Role / Department` as the first column.

### Output schema

{{schema}}

### Analysis

<untrusted_analysis id="{{nonce}}">
{{analysis}}
</untrusted_analysis id="{{nonce}}">

Now produce the JSON object containing only the revised sections.
