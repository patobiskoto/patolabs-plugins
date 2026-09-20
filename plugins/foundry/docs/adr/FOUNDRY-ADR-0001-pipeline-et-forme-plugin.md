---
type: adr
id: FOUNDRY-ADR-0001
title: "Foundry — pipeline idée→livraison + forme plugin"
status: proposed
date: 2026-07-01
supersedes: null
superseded_by: null
amended_by: [FOUNDRY-ADR-0002, FOUNDRY-ADR-0003, FOUNDRY-ADR-0004]
deciders: ["pato"]
context_tags: [foundation, methodology, plugin, youtrack]
---

# FOUNDRY-ADR-0001 — Le pipeline et sa forme (plugin `foundry@patolabs`)

> ADR fondateur. Il pose le cadre que les ADR suivants ne rouvrent pas.
> Vit dans le repo tant que le projet YouTrack `FOUNDRY` n'existe pas (bootstrap
> chicken-and-egg) ; migré/répliqué en KB au provisioning.

## Contexte

Le pilotage projet actuel (skills `~/.claude/youtrack/` + tooling Python parlant à
YouTrack) souffre de quatre limites structurelles, vérifiées dans le code :

1. **Le jugement est cuit dans le Python.** `next_issue.py` charge un `FIELDS` sans
   `links` ni AC, fait `sorted()` puis `print(ranked[0])` : Claude ne voit jamais le
   backlog, il relaie un verdict déjà décidé. Les skills « trient et comptent », ils
   ne raisonnent pas.
2. **Aucune mémoire inter-features.** Rien ne charge les décisions passées avant d'en
   rouvrir une. La règle « vérifie les ADR avant un choix d'archi » est un vœu (prose),
   pas un mécanisme exécuté.
3. **Invariants mal placés.** La « CI verte avant merge » vit dans la prose de
   `merge-pr.md` ; `skill.py merge()` fait le `PUT /merge` sans vérifier les check-runs.
   Une session pressée peut merger rouge.
4. **Non distribuable.** Chemins hardcodés (`~/.claude/youtrack/`), token en clair sur
   disque (`~/.config/orfeo-poc/youtrack.env`), registre `PROJECTS` édité à la main.
   Le stack est lié à une machine, non installable ni versionnable proprement.

Besoin cible (formulé par le porteur) : démarrer d'une petite idée → **brainstorm
profond guidé** → dès que c'est cadré, **historiser en ADR + issues précises et sans
ambiguïté** → **roadmap par la valeur** (la plus petite tranche qui démontre le
produit) → **dérouler issue après issue** dans un workflow gated → **intake** des
idées en cours de route (backlog / raffinage) **sans jamais revalider l'acquis** — le
tout **robuste et répétable sur tous les projets**.

## Décision

Construire **Foundry** : un **plugin Claude Code** (`foundry`, marketplace `patolabs`),
repo git dédié, projet YouTrack `FOUNDRY`, qui se pilote avec sa propre méthode.

Cinq partis pris structurants :

### 1. Modèle à deux boucles
- **Boucle interne** (design→plan→impl, *dans* un ticket) : déléguée à
  **Superpowers** (brainstorm profond guidé, writing-plans) et/ou **Plan mode** natif.
  TDD **conditionnel au domaine** (parfait pour la logique pure ; pas imposé à l'UI,
  aux migrations, à SwiftUI). Foundry n'y touche pas.
- **Boucle externe** (quoi faire, état de vérité, mémoire, gates de sortie) :
  **Foundry + YouTrack**. C'est ce que Superpowers n'a pas et ne cherche pas à avoir.

### 2. Retrieval-avant-raisonnement = le mécanisme de « ne pas revalider »
Tout skill qui pourrait rouvrir une question **charge d'abord** les ADR `accepted` +
les specs d'issues pertinents (tier query) et les **traite comme acquis**, sauf
`supersede` explicite. C'est une **précondition**, pas une option. La garantie
« je ne rediscute pas ce qui est validé » vient de la couche persistante (ADR = cadre
gelé, AC testable = périmètre gelé) — **jamais** de la boucle interne.

### 3. Frontière query / write
- **Tier `query`** (nouveau, `tooling/query.py`) : renvoie du **JSON riche** (issues +
  liens + AC + PR + timestamps + rank déterministe comme *champ*), **zéro décision,
  zéro prose**. C'est ce que consomment les skills de jugement. *(Amendé par
  FOUNDRY-ADR-0003 : les listes sont maigres — le signal toujours, les corps de
  texte à la demande via `query issue <ID>` / `query adr <ID>`.)*
- **Tier `write`** (`skill.py` étendu) : setters déterministes + **invariants
  mécaniques dans le code** (refuse le merge si la CI n'est pas prouvée verte —
  clause amendée par FOUNDRY-ADR-0002 : ≥ 1 `success`, rien de rouge ni d'en
  cours, `neutral`/`skipped` tolérés à côté ; transitions d'état légales ; pas
  de push direct). `set_field()` générique pour
  fermer les boucles — **jamais auto-appelé sans confirmation humaine**.

### 4. Deux natures de skills
- **Mécaniques** (`start-issue`, `open-pr`, `merge-pr`, `adr`, `doctor`) : wrappers
  fins, transitions prévisibles, gates en prose + invariants en code.
- **Jugement** (`frame`, `next-issue`, `roadmap`, `groom`, `intake`, `blockers`) :
  le `.md` dit à Claude d'appeler le tier query, de **raisonner** sur le graphe JSON,
  de produire une reco justifiée, puis de proposer des écritures **confirmées par
  l'humain** qui repassent par les setters déterministes. Le rank priorité→estimate→
  date devient **un signal**, pas la réponse.

### 5. Forme plugin (répare les fragilités en packageant)
- `${CLAUDE_PLUGIN_ROOT}` → fin des chemins hardcodés (`python3 ${CLAUDE_PLUGIN_ROOT}/tooling/…`).
- `userConfig` → URL + token YouTrack demandés à l'install ; le token (`sensitive:true`)
  vit **dans le keychain** (fin du `.env` en clair). Le tooling les lit via les env
  vars `CLAUDE_PLUGIN_OPTION_*`. Fallback fichier `~/.config` en dev.
- `${CLAUDE_PLUGIN_DATA}` → foyer du registre repo→projet (fin de l'édition manuelle).
  *(Amendé par FOUNDRY-ADR-0005 : le registre vit dans `FOUNDRY_DATA` sinon
  `~/.config/foundry` — les dossiers privés du plugin ne sont pas partagés entre
  hooks et commandes de skills.)*
- Embarque l'agent `reviewer` et le hook `pre-push`. Repo = son propre marketplace.
  *(« Repo = son propre marketplace » remplacé par FOUNDRY-ADR-0004 : le catalogue
  patolabs vit dans le monorepo `claude-plugins`, Foundry y est un plugin.)*

### 6. Providers pluggables (tracker + code-host = adaptateurs)
Même philosophie que le `CurationEngine` d'Orfeo : le pipeline et les skills raisonnent
sur des **modèles normalisés** (`Issue`, `Adr`, `PR`, `Check`) via deux interfaces —
`Tracker` (issues, ADR, champs, états) et `CodeHost` (PR, check-runs, merge). Seuls les
**adaptateurs** touchent l'API concrète. v1 : adaptateurs **YouTrack** + **GitHub**
réels, **+ un stub GitHub Projects** qui prouve la couture. Basculer vers Jira / GitLab /
Projects plus tard = écrire un adaptateur, **zéro réécriture** du pipeline ou des skills.
Le tracker et le code-host actifs sont des **paramètres** (config), pas des constantes.

## Le pipeline (4 phases)

| Phase | Skill | Rôle | Propriété |
|---|---|---|---|
| 0. Genesis | *(Superpowers)* → **`/foundry:frame`** | brainstorm profond guidé, puis **matérialise** le design en ADR (`proposed`) + épic + issues précises à AC testable, liées aux ADR qui les contraignent | pont à construire |
| 1. Séquence | **`/foundry:roadmap`** | ordonne par valeur : la plus petite tranche démontrant le produit, en respectant dépendances + ADR | jugement, raisonne sur le graphe |
| 2. Exécution | `next-issue`→`start-issue`→*(boucle interne)*→`open-pr`→`merge-pr` | déroule, gated ; consultation ADR pendant l'implémentation | mécaniques + invariants |
| 3. Intake | **`/foundry:intake`** | idée en route → nouvelle issue / raffine / nouvel ADR / **rejeté car ADR-X a tranché** | jugement gardé par consultation ADR |

## Conséquences

**Devient possible :** un flux idée→livré répétable et versionné, installable sur tout
projet en deux commandes ; des issues sans ambiguïté (AC = contrat) ; une roadmap
dérivée de la valeur, pas décrétée ; une mémoire de décisions qui empêche de
retourner sur l'acquis ; des gates non contournables (invariant CI en code).

**Devient interdit / contraint :** la création de PR passe **uniquement** par
`/foundry:open-pr` ; le merge **uniquement** par `/foundry:merge-pr` (jamais
`gh pr merge`, jamais la PR auto de Superpowers) ; ces contrats sont déclarés dans
l'`AGENTS.md` de chaque projet consommateur pour empêcher la boucle interne de
court-circuiter le tracker.

**Devient plus coûteux :** une dépendance (souple) à Superpowers pour la boucle
interne ; l'écriture des skills de jugement demande plus de soin qu'un `print()`.

**Dette de portage à solder au scaffold** (bugs vérifiés dans le stack actuel) :
ticker ADR depuis `current_project()` (adr.py hardcode `ORFEO-ADR` + scanne
`/articles` non filtré → contamination cross-projet) ; `owner_repo()` robuste aux
alias SSH ; scope PR dérivé (openpr hardcode `(mac)`) ; `sprints[0]` → sprint résolu ;
registre `PROJECTS` → `${CLAUDE_PLUGIN_DATA}`.

## Alternatives écartées

- **Adopter Superpowers en bloc comme socle unique** — il n'a ni tracking, ni ADR, ni
  merge gate (tracker-agnostic par design) ; il ne peut pas fournir « ne pas
  revalider ». On prend ses idées (brainstorm/plan) et son format, pas une dépendance
  dure qui posséderait les parties pour lesquelles il n'est pas fait.
- **Garder les skills dans `~/.claude/`** — non distribuable, non versionnable,
  chemins et token liés à la machine. Le plugin est la seule forme « installable
  partout + maintenue dans son repo ».
- **Fusionner dans `claude-project-template`** — le template est du contenu *copié*
  par projet ; Foundry doit être *installé* et *mis à jour* centralement depuis son
  repo. Deux artefacts, deux cycles de vie.
- **Retrieval ADR sophistiqué (embeddings/index)** — à 15-30 ADR/projet, on charge
  tout et le modèle filtre. Rester dumb jusqu'à ce que l'échelle l'exige. *(Amendé
  par FOUNDRY-ADR-0003 : index titres+statuts + drill-down par id — toujours pas
  d'embeddings, mais on ne charge plus tout.)*

## Non-goals (v1)

Boucle interne réinventée (déléguée) · UI/dashboard (YouTrack la fournit) · CI qui
repatch l'issue (retiré, le merge gate suffit). Le multi-tracker/multi-code-host **n'est
plus** un non-goal : c'est le parti pris #6 (seam + adaptateurs). v1 implémente
YouTrack + GitHub + 1 stub ; les autres providers viendront par adaptateur.

## Décisions de scaffold (tranchées le 2026-07-01)

- **Ticker YouTrack** : `FOUNDRY`.
- **ADR de Foundry** : repo `docs/adr/` en bootstrap → réplique en KB YouTrack quand le
  projet `FOUNDRY` est provisionné (dogfooding complet).
- **`frame`** : un seul skill genesis (brainstorm guidé intégré, compose avec Superpowers
  si présent) qui matérialise ADR + issues. Pas de skill de cadrage amont séparé.
- **POC de vérification** : projet-jouet **séparé** (`mdtoc`, CLI Markdown TOC) avec son
  propre repo privé + projet YouTrack, pour live-firer le pipeline complet (frame →
  roadmap → exécution → PR → CI → merge) sans polluer le tracker de Foundry.
- **Repos** : Foundry et le POC sont des **repos GitHub privés** dédiés.
