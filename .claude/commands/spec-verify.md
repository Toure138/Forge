---
description: "SDD étape 5 : vérifier une spec implémentée face à ses critères d'acceptation"
argument-hint: <NNN>
---

Étape 5 du workflow SDD : **vérifier**. Spec : `$ARGUMENTS`.

1. Délègue au sous-agent `reviewer` : « Vérifie la spec <chemin> et écris review.md. »
2. Présente le verdict, le tableau des AC et les problèmes classés par gravité.
3. Si des changements sont requis, propose les tâches correctives à ajouter à `tasks.md` et attends l'accord avant de relancer `/spec-implement`.
4. Si le verdict est APPROUVÉ, rappelle que la spec est `done` et propose un commit ou une PR, sans les faire sans demande.
