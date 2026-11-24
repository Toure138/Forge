---
description: "SDD étape 4 : implémenter les tâches d'une spec, en TDD"
argument-hint: <NNN> [T00x | phase-N | all]
---

Étape 4 du workflow SDD : **implémenter**. Arguments : `$ARGUMENTS`.

Portée :
- `<NNN>` seul : la **prochaine phase** contenant des tâches non cochées ;
- `<NNN> T003` : uniquement cette tâche ;
- `<NNN> phase-2` : cette phase ;
- `<NNN> all` : tout ce qui reste, avec un point d'étape après chaque phase.

Procédure :
1. Vérifie que la spec est en `ready` ou en `in-progress`, puis passe-la en `in-progress`.
2. Pour chaque tâche de la portée, dans l'ordre :
   - délègue au sous-agent de sa couche : `agent` → `agent-engineer`, `backend` → `backend-engineer`, `frontend` → `frontend-engineer`, `docs` → toi-même ;
   - transmets la tâche exacte (ID, description, test attendu, AC couverts) et les chemins de `spec.md`, `plan.md` et `tasks.md` ;
   - les tâches `[P]` d'une même phase peuvent être déléguées en parallèle ;
   - à son retour, vérifie que la case est cochée et que le test existe.
3. Si un sous-agent signale un écart avec la spec ou le plan, **arrête-toi** et présente la mise à jour proposée du document.
4. À la fin de la portée, lance `powershell -File scripts/check.ps1` depuis la racine.
5. Rapporte les tâches faites, les tests ajoutés, le résultat de check.ps1, ce qui reste, et un message de commit proposé (Conventional Commit avec `[spec-NNN]`). Ne commite pas sans demande.
6. Si toutes les tâches sont cochées et que tout est vert, passe la spec en `review` et propose `/spec-verify <NNN>`.
