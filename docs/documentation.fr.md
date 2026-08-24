# Action Control — Documentation

*[English](documentation.md) | [Français](documentation.fr.md)*

Cette page est la référence détaillée d'**Action Control**. Pour un
aperçu rapide, la liste des fonctionnalités et l'installation, voir le
[README principal](../README.fr.md).

## Sommaire

- [Fonctionnement](#fonctionnement)
- [Référence des champs de règle](#référence-des-champs-de-règle)
- [Exemples](#exemples)
- [Journalisation de débogage](#journalisation-de-débogage)
- [FAQ](#faq)

## Fonctionnement

Action Control écoute l'événement interne `call_service` de Home
Assistant — l'événement émis pour *chaque* appel de service, quelle que
soit son origine (une personne, une automation, un script, une autre
intégration). Pour chaque règle configurée, sur un appel correspondant :

1. **Résout les entités ciblées** — à partir d'`entity_id`, `device_id`,
   `area_id` et/ou `label_id` de l'appel, via les registres
   entité/appareil (la même résolution que les automations d'origine
   faisaient à la main, généralisée à n'importe quel domaine).
2. **Vérifie une correspondance immédiate.** Si l'entité reflète déjà
   l'état/les attributs demandés au moment même où l'événement se
   déclenche (commande sans effet, ou déjà appliquée instantanément par
   l'intégration cible), la règle se résout immédiatement — sans délai,
   sans notification.
3. Sinon, selon le mode :
   - **Mode instantané** (par défaut) : attend `check_delay` secondes,
     puis compare l'état/les attributs de l'entité à ce qui a été
     demandé, avec tolérance. En cas d'écart, la règle relance la
     commande et retente jusqu'à `retries` fois, espacées de
     `retry_delay` secondes.
   - **Mode mouvement** (`wait_for_change`, utilisé pour les volets) :
     au lieu de comparer un instantané, attend jusqu'à `change_timeout`
     secondes que `change_attribute` commence réellement à changer. Si ce
     n'est pas le cas, c'est l'échec — la relance et les retentatives
     fonctionnent de la même façon.
4. **En cas d'échec persistant**, si l'escalade est activée et que son
   délai de recharge est écoulé : exécute l'action de secours configurée,
   attend `escalation_replay_delay` secondes, puis rejoue une dernière
   fois la commande d'origine.
5. **Notifie** (notification persistante et/ou service `notify.*`) en
   précisant ce qui était attendu par rapport à ce qui a été observé.

Chaque commande réémise par Action Control (relance, ou rejeu après
escalade) porte un `Context` Home Assistant propre, mémorisé en interne.
Le déclencheur reconnaît et ignore tout événement `call_service` portant
un de ces contextes auto-émis *avant* tout traitement — c'est ce qui
empêche une relance de se re-déclencher elle-même ou une autre règle,
sans entité de garde ni configuration supplémentaire. Cette mémoire est
volontairement limitée au processus en cours (un redémarrage de Home
Assistant la vide) — il n'y a jamais rien de significatif à conserver
d'un redémarrage à l'autre, puisqu'un redémarrage arrête de toute façon
toute vérification en cours.

## Référence des champs de règle

### Ciblage

| Champ | Description |
|---|---|
| Nom | Libellé affiché sur le capteur de statut de la règle et dans les notifications. |
| Domaines | Un ou plusieurs domaines surveillés par cette règle (ex. `light`, `switch`, `cover`). Obligatoire. |
| Services | Services surveillés dans ces domaines (ex. `turn_on`). Les suggestions correspondent à l'union des services réellement enregistrés pour les domaines choisis. Laisser vide pour surveiller tous les services du domaine. |
| Motif d'entity_id | Motif glob optionnel (ex. `cover.volet_*`) que l'`entity_id` doit respecter. |
| Motif de nom convivial | Motif glob optionnel comparé au nom de l'entité. |
| Pièces / Étiquettes / Appareils | Filtres optionnels — une entité correspond si elle (ou son appareil) appartient à une des pièces/étiquettes/appareils sélectionnés. |

Une règle sans aucun filtre motif/pièce/étiquette/appareil correspond à
toutes les entités du/des domaine(s)/service(s) choisis — par exemple
« surveiller toutes les lumières ».

### Vérification

| Champ | Description | Par défaut |
|---|---|---|
| Délai avant la première vérification | Secondes d'attente après la commande avant la première comparaison (mode instantané uniquement). | 2 |
| Attributs à vérifier | Attributs comparés en plus de l'état (ex. `brightness`, `rgb_color`). | aucun |
| Tolérances | `attribut:valeur, attribut2:valeur2` — tolérance numérique par attribut. Les attributs de type liste (comme `rgb_color`) appliquent la tolérance par élément. | aucune (égalité stricte) |
| Nombre de relances | Combien de fois relancer la commande si la vérification échoue. | 2 |
| Délai entre les relances | Secondes entre chaque relance. | 2 |
| Attendre un changement | Bascule en mode mouvement : attend que `change_attribute` change réellement plutôt que de comparer un instantané. | désactivé |
| Attribut à surveiller | L'attribut surveillé par le mode mouvement (ex. `current_position`). | — |
| Délai d'attente du changement | Secondes à attendre avant de considérer que le changement a échoué. | 45 |

Les domaines `light`, `switch` et `cover` sont préremplis automatiquement
avec des valeurs par défaut adaptées (light : brightness/rgb_color/
color_temp_kelvin/xy_color avec tolérance ; switch : état seul ; cover :
mode mouvement sur `current_position`). Tout autre domaine part d'une
simple vérification d'état, à affiner avec les champs ci-dessus.

### Escalade et notifications

| Champ | Description | Par défaut |
|---|---|---|
| Activer l'action d'escalade | Active l'étape d'action de secours après échec persistant. | désactivé |
| Action d'escalade | N'importe quelle séquence d'actions Home Assistant (appel de service, script...) — utilise le même éditeur d'action que les automations. | — |
| Délai minimum entre deux escalades | Délai de recharge en secondes avant qu'une même règle puisse escalader à nouveau. | 300 |
| Délai avant de rejouer | Secondes d'attente après l'action d'escalade avant de rejouer la commande d'origine. | 90 |
| Notification persistante | Crée une `persistent_notification` en cas d'échec final. | activé |
| Service notify | Appelle également ce service `notify.*` en cas d'échec final. | — |

## Exemples

### Surveillance de lumières

- Domaines : `light`
- Services : `turn_on`, `turn_off`, `toggle` (ou vide pour tous)
- Attributs à vérifier : `brightness`, `rgb_color` (préremplis par défaut)
- Relances : 2, délai 2s

Vérifie que la luminosité/couleur demandées ont bien été appliquées, avec
tolérance, et relance en cas d'écart — généralise l'automation d'origine
lumières/prises à n'importe quelle lumière.

### Surveillance de volets / redémarrage de passerelle (façon KLF200)

- Domaines : `cover`
- Motif d'entity_id : `cover.volet_*`
- Attendre un changement : activé, attribut `current_position`, délai 45s
- Escalade : activée, action = `switch.turn_on` sur le switch de
  redémarrage de votre passerelle, délai de recharge 300s, délai de
  rejeu 90s

Attend qu'un volet commence réellement à bouger ; si ce n'est pas le cas
après les relances, active le switch de redémarrage de la passerelle,
attend, puis rejoue la commande d'origine — généralise l'automation
KLF200, le délai de recharge remplaçant entièrement l'ancien switch de
garde externe.

## Journalisation de débogage

Rien ne nécessite de redémarrage pour essayer : Outils de développement
→ Actions → appeler le service `logger.set_level` avec :

```yaml
custom_components.action_control: debug
```

Pour un réglage permanent, ajoutez dans `configuration.yaml` puis
redémarrez complètement Home Assistant :

```yaml
logger:
  logs:
    custom_components.action_control: debug
```

Au niveau debug, vous verrez quelles règles un appel de service a
déclenchées, quelles entités sont surveillées et avec quel état/attributs
attendus, chaque tentative de vérification/relance avec ses écarts,
l'escalade et le rejeu, ainsi que les notifications envoyées. L'échec
final de vérification d'une règle est toujours journalisé au niveau
**warning**, donc visible même sans debug activé.

## FAQ

**La mémoire anti-boucle survit-elle à un redémarrage de Home Assistant ?**
Non, et ce n'est pas un problème — c'est un registre en mémoire avec une
courte durée de vie, et un redémarrage arrête de toute façon toute
vérification en cours, donc il n'y a rien de significatif à protéger
d'un redémarrage à l'autre.

**Si je choisis deux domaines, les services suggérés sont-ils combinés ?**
Oui — l'étape « quels services » suggère l'union de tous les services
réellement enregistrés pour les domaines choisis (ex. `light` + `switch`
suggère `turn_on`/`turn_off`/`toggle` fusionnés, sans doublon). Vous
pouvez toujours saisir un nom de service non suggéré.

**Pourquoi je ne vois pas l'icône de l'intégration dans Home Assistant ?**
L'icône est embarquée dans `custom_components/action_control/brand/` et
servie automatiquement via l'[API Brands Proxy](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api)
de Home Assistant, qui nécessite **Home Assistant 2026.3.0 ou plus
récent**. Sur une version plus ancienne, l'icône ne s'affichera pas, mais
l'intégration fonctionne de la même façon dans les deux cas.
