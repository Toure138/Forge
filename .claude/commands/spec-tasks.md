---
description: "SDD étape 3 : découper un plan validé en tâches TDD"
argument-hint: <NNN>
---

Étape 3 du workflow SDD : **découper**. Spec : `$ARGUMENTS`.

1. Vérifie que `specs/<NNN>-*/plan.md` existe et que la spec est en `status: planned`. Sinon, indique l'étape manquante et arrête-toi.
2. Délègue au sous-agent `architect` : « Écris tasks.md pour la spec <chemin>, à partir de son plan.md. »
3. Vérifie que chaque AC est couvert par au moins une tâche, que les tâches sont petites et ordonnées, et que les `[P]` sont vraiment indépendants.
4. Présente le nombre de tâches par phase et par couche, et l'ordre proposé.
5. **Arrête-toi** et attends la validation. L'étape suivante est `/spec-implement <NNN>`.
