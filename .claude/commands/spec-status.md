---
description: Afficher l'avancement de toutes les specs
---

Liste chaque dossier `specs/NNN-*/` (hors `_templates`). Pour chacun, lis le frontmatter de `spec.md` (`id`, `title`, `status`) et compte les cases `- [x]` et `- [ ]` dans `tasks.md`, s'il existe.

Affiche un tableau : `| NNN | titre | statut | tâches faites/total | prochaine action |`.

La prochaine action se déduit du statut : `draft` → répondre aux [À CLARIFIER] puis `/spec-plan` · `planned` → `/spec-tasks` · `ready` ou `in-progress` → `/spec-implement` · `review` → `/spec-verify` · `done` → rien.

Ne modifie aucun fichier.
