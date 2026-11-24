---
description: "SDD étape 2 : écrire le plan technique d'une spec validée"
argument-hint: <NNN> [contraintes techniques supplémentaires]
---

Étape 2 du workflow SDD : **planifier**. Arguments : `$ARGUMENTS` (le premier mot est le numéro de spec).

1. Trouve `specs/<NNN>-*/spec.md`. S'il reste des `[À CLARIFIER]`, liste-les et arrête-toi.
2. Délègue au sous-agent `architect` : « Écris plan.md pour la spec <chemin>. » Transmets aussi les contraintes supplémentaires éventuellement passées en argument.
3. Relis le plan. Chaque AC doit avoir un test associé, la conformité à la constitution doit être remplie et les fichiers impactés doivent être réalistes.
4. Présente un résumé de l'approche, les choix clés (avec les alternatives écartées), les ADR proposés, les nouvelles dépendances et les risques.
5. **Arrête-toi** et attends la validation. L'étape suivante est `/spec-tasks <NNN>`.
