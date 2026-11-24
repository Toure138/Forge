# Specs

Une fonctionnalité = un dossier `NNN-slug/` (numéro à trois chiffres, incrémental) :

```
specs/003-web-search-tool/
├── spec.md     # QUOI et POURQUOI — validé par l'humain       (/spec-new)
├── plan.md     # COMMENT — architecture, contrat, tests       (/spec-plan)
├── tasks.md    # découpage en tâches TDD ordonnées            (/spec-tasks)
└── review.md   # vérification des critères d'acceptation      (/spec-verify)
```

Les modèles sont dans [_templates/](_templates/). Le cycle de vie complet est décrit dans [../CLAUDE.md](../CLAUDE.md).

| Statut | Signification |
|---|---|
| `draft` | spec en cours d'écriture ou en attente de validation |
| `planned` | spec validée, `plan.md` écrit |
| `ready` | `tasks.md` écrit, prêt à implémenter |
| `in-progress` | implémentation en cours |
| `review` | implémentation terminée, en vérification |
| `done` | tous les critères d'acceptation sont vérifiés |
