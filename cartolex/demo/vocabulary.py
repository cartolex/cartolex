# SPDX-License-Identifier: MIT
"""Vocabulary of the invented coastal and marine research community.

Twelve themes, each a list of technical terms written as ``English | French``
lines. The French form carries its definite article (``le``, ``la``, ``l'``,
``les``) so that the text templates can build grammatical French: ``du``,
``de la``, ``de l'``, ``des``, ``au``, ``à la``, ``aux``. A term listed in two
themes (same English form, same French form) is a *bridge* term: themes
overlap, as they do in a real field.

Everything here is data: importing the module parses the lists and checks
their shape, nothing else.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

ARTICLES = ("le", "la", "l'", "les")
# Percentage of object-aspect pairs of a family kept as compound terms.
COMPOUND_DENSITY = 100


@dataclass(frozen=True)
class Term:
    """A technical term in English and French.

    ``fr`` is the bare French form (no article); ``article`` is its definite
    article, one of :data:`ARTICLES`.
    """

    en: str
    fr: str
    article: str
    technique: bool = False  # a tool or approach rather than a phenomenon or topic
    focus: str = ""  # for a compound term, the object it is about ("hake")

    @property
    def fr_def(self) -> str:
        """French form with its definite article (``l'érosion côtière``)."""
        return f"l'{self.fr}" if self.article == "l'" else f"{self.article} {self.fr}"

    @property
    def fr_de(self) -> str:
        """French form after ``de`` (``du``, ``de la``, ``de l'``, ``des``)."""
        return {
            "le": f"du {self.fr}",
            "la": f"de la {self.fr}",
            "l'": f"de l'{self.fr}",
            "les": f"des {self.fr}",
        }[self.article]

    @property
    def fr_a(self) -> str:
        """French form after ``à`` (``au``, ``à la``, ``à l'``, ``aux``)."""
        return {
            "le": f"au {self.fr}",
            "la": f"à la {self.fr}",
            "l'": f"à l'{self.fr}",
            "les": f"aux {self.fr}",
        }[self.article]


@dataclass(frozen=True)
class Theme:
    """One research theme of the invented field."""

    id: str
    name_en: str
    name_fr: str
    kind: str  # "natural" or "social": picks the family of sentence templates
    neighbours: tuple[str, ...]  # related themes, most related first
    group_stems: tuple[str, ...]  # words for the names of groups working on it
    terms: tuple[Term, ...]

    @property
    def topics(self) -> tuple[Term, ...]:
        """Terms naming phenomena or topics (what a work is about)."""
        return tuple(t for t in self.terms if not t.technique)

    @property
    def techniques(self) -> tuple[Term, ...]:
        """Terms naming tools or approaches (how a work is done)."""
        return tuple(t for t in self.terms if t.technique)


@dataclass(frozen=True)
class Method:
    """A method term, usable by natural-science works, social-science works or both."""

    term: Term
    kind: str  # "natural", "social" or "any"


@dataclass(frozen=True)
class Setting:
    """A place where a study happens, as a locative phrase in both languages.

    ``social`` marks settings that also suit social-science works (a coastline
    people live on, not the open ocean).
    """

    en: str
    fr: str
    social: bool = False


def parse_terms(block: str) -> tuple[Term, ...]:
    """Parse ``English | French-with-article`` lines into terms.

    A leading ``~`` marks a technique (a tool or approach: the templates use
    it where a method goes). Blank lines and lines starting with ``#`` are
    skipped. Raises :class:`ValueError` on a line without exactly one ``|`` or
    a French form without a definite article.
    """
    terms: list[Term] = []
    for raw in block.strip().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        technique = line.startswith("~")
        line = line.lstrip("~ ")
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"bad term line: {line!r}")
        en, fr = parts
        if fr.startswith("l'"):
            terms.append(Term(en, fr[2:], "l'", technique))
            continue
        article, _, rest = fr.partition(" ")
        if article not in ("le", "la", "les") or not rest:
            raise ValueError(f"French form needs a definite article: {line!r}")
        terms.append(Term(en, rest, article, technique))
    return tuple(terms)


# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------

_GEOMORPHOLOGY = """
sediment transport | le transport sédimentaire
longshore drift | la dérive littorale
cross-shore sediment exchange | les échanges sédimentaires transversaux
beach morphodynamics | la morphodynamique des plages
sandbar migration | la migration des barres sableuses
dune erosion | l'érosion dunaire
foredune recovery | la régénération des avant-dunes
aeolian sand transport | le transport éolien de sable
shoreline change | l'évolution du trait de côte
~ beach profile surveys | les levés de profils de plage
grain size distribution | la distribution granulométrique
bedload transport | le transport par charriage
suspended sediment concentration | la concentration en matières en suspension
ripple formation | la formation des rides sédimentaires
tidal inlet dynamics | la dynamique des embouchures tidales
ebb-tidal delta | le delta de jusant
spit elongation | l'allongement des flèches littorales
barrier island migration | la migration des îles-barrières
shore platform erosion | l'érosion des plates-formes littorales
cliff retreat | le recul des falaises
rock coast erosion | l'érosion des côtes rocheuses
gravel beach dynamics | la dynamique des plages de galets
sediment budget | le bilan sédimentaire
littoral sediment cells | les cellules hydrosédimentaires
nearshore bathymetry | la bathymétrie de l'avant-côte
wave-driven currents | les courants induits par les vagues
rip currents | les courants d'arrachement
swash zone processes | les processus de la zone de jet de rive
surf zone hydrodynamics | l'hydrodynamique de la zone de déferlement
sediment sorting | le tri granulométrique
mixed sand-gravel beaches | les plages mixtes de sable et de galets
beach nourishment | le rechargement en sable des plages
~ morphodynamic modelling | la modélisation morphodynamique
equilibrium beach profiles | les profils de plage d'équilibre
storm-induced erosion | l'érosion induite par les tempêtes
overwash deposits | les dépôts de débordement
sediment provenance | la provenance des sédiments
shoreface dynamics | la dynamique de l'avant-côte
subtidal sandbanks | les bancs sableux subtidaux
sand wave migration | la migration des dunes sous-marines
tidal flat morphology | la morphologie des estrans vaseux
cohesive sediment dynamics | la dynamique des sédiments cohésifs
flocculation processes | les processus de floculation
erosion thresholds | les seuils d'érosion
critical shear stress | la contrainte de cisaillement critique
bed shear stress | la contrainte de cisaillement sur le fond
sediment resuspension | la remise en suspension des sédiments
coastal morphodynamics | la morphodynamique côtière
~ shoreline mapping | la cartographie du trait de côte
beach rotation | la rotation des plages
headland bypassing | le contournement des caps
embayed beaches | les plages de poche
~ sediment tracer experiments | les expériences de traçage sédimentaire
~ heavy mineral analysis | l'analyse des minéraux lourds
fluvial sediment supply | les apports sédimentaires fluviaux
delta progradation | la progradation des deltas
coastal dune vegetation | la végétation des dunes littorales
dune blowout dynamics | la dynamique des caoudeyres
~ video beach monitoring | le suivi vidéo des plages
beach cusps | les croissants de plage
berm formation | la formation des bermes
longshore sediment flux | le flux sédimentaire longitudinal
morphological equilibrium | l'équilibre morphologique
sediment connectivity | la connectivité sédimentaire
shingle ridges | les cordons de galets
dune stabilisation | la stabilisation des dunes
coastal geomorphology | la géomorphologie côtière
wave runup | la hauteur de jet de rive
infragravity waves | les ondes infragravitaires
sandbar welding | le rattachement des barres au haut de plage
~ shoreline response modelling | la modélisation de la réponse du trait de côte
~ grain shape analysis | la morphoscopie des grains
beach groundwater | la nappe de plage
~ cliff monitoring | le suivi des falaises côtières
marine aggregate extraction | l'extraction de granulats marins
"""

_CIRCULATION = """
~ tidal harmonic analysis | l'analyse harmonique de la marée
internal tides | les marées internes
tidal asymmetry | l'asymétrie de la marée
tidal currents | les courants de marée
residual circulation | la circulation résiduelle
shelf circulation | la circulation sur le plateau continental
coastal upwelling | l'upwelling côtier
Ekman transport | le transport d'Ekman
mesoscale eddies | les tourbillons de méso-échelle
submesoscale fronts | les fronts de sous-méso-échelle
geostrophic currents | les courants géostrophiques
western boundary currents | les courants de bord ouest
thermohaline circulation | la circulation thermohaline
water mass formation | la formation des masses d'eau
vertical mixing | le mélange vertical
turbulent kinetic energy | l'énergie cinétique turbulente
stratification dynamics | la dynamique de la stratification
mixed layer depth | la profondeur de la couche de mélange
river plume dynamics | la dynamique des panaches fluviaux
estuarine circulation | la circulation estuarienne
wind-driven circulation | la circulation induite par le vent
storm surge propagation | la propagation des surcotes de tempête
tidal energy dissipation | la dissipation de l'énergie de marée
shelf sea dynamics | la dynamique des mers épicontinentales
coastal trapped waves | les ondes piégées à la côte
Kelvin wave propagation | la propagation des ondes de Kelvin
inertial oscillations | les oscillations inertielles
baroclinic instability | l'instabilité barocline
frontal systems | les systèmes frontaux
tidal mixing fronts | les fronts de mélange tidal
ocean heat content | le contenu thermique de l'océan
sea surface temperature | la température de surface de la mer
salinity gradients | les gradients de salinité
density-driven flows | les écoulements induits par la densité
~ hydrodynamic modelling | la modélisation hydrodynamique
~ regional ocean models | les modèles océaniques régionaux
~ unstructured grid models | les modèles à maillage non structuré
~ open boundary conditions | les conditions aux limites ouvertes
~ current profiling | le profilage des courants
~ drifter trajectories | les trajectoires de bouées dérivantes
~ Lagrangian particle tracking | le suivi lagrangien de particules
water residence time | le temps de résidence des eaux
larval connectivity | la connectivité larvaire
slope currents | les courants de pente
canyon upwelling | l'upwelling dans les canyons sous-marins
internal wave breaking | le déferlement des ondes internes
harbour seiches | les seiches portuaires
sea level variability | la variabilité du niveau marin
~ tide gauge records | les enregistrements marégraphiques
~ tidal prediction | la prédiction de la marée
wave-current interactions | les interactions houle-courant
Stokes drift | la dérive de Stokes
Langmuir circulation | la circulation de Langmuir
air-sea fluxes | les flux air-mer
wind stress forcing | le forçage par la tension du vent
~ ocean reanalysis products | les produits de réanalyse océanique
meridional overturning circulation | la circulation méridienne de retournement
potential vorticity | la vorticité potentielle
topographic steering | le guidage topographique des courants
tidal rectification | la rectification tidale
freshwater fluxes | les apports d'eau douce
coastal jets | les jets côtiers
eddy kinetic energy | l'énergie cinétique tourbillonnaire
~ vertical velocity estimates | les estimations de vitesse verticale
tidal resonance | la résonance tidale
nonlinear tidal constituents | les composantes non linéaires de la marée
~ ocean circulation modelling | la modélisation de la circulation océanique
~ mooring arrays | les réseaux de mouillages
~ hydrographic sections | les sections hydrographiques
deep water formation | la formation des eaux profondes
"""

_ECOLOGY = """
benthic communities | les communautés benthiques
seagrass meadows | les herbiers de phanérogames marines
kelp forests | les forêts de laminaires
coral reef resilience | la résilience des récifs coralliens
~ species distribution models | les modèles de distribution d'espèces
functional diversity | la diversité fonctionnelle
trophic interactions | les interactions trophiques
food web structure | la structure des réseaux trophiques
marine protected areas | les aires marines protégées
~ habitat mapping | la cartographie des habitats
non-indigenous species | les espèces non indigènes
biological invasions | les invasions biologiques
population connectivity | la connectivité des populations
larval dispersal | la dispersion larvaire
recruitment variability | la variabilité du recrutement
intertidal zonation | la zonation intertidale
rocky intertidal assemblages | les peuplements intertidaux rocheux
soft-sediment macrofauna | la macrofaune des sédiments meubles
bioturbation processes | les processus de bioturbation
ecosystem engineers | les espèces ingénieures
biogenic reefs | les récifs biogènes
oyster reefs | les récifs d'huîtres
~ environmental DNA | l'ADN environnemental
~ metabarcoding surveys | les inventaires par métabarcodage
species richness | la richesse spécifique
beta diversity | la diversité bêta
community assembly | l'assemblage des communautés
ocean warming impacts | les impacts du réchauffement océanique
marine heatwaves | les vagues de chaleur marines
thermal tolerance | la tolérance thermique
calcifying organisms | les organismes calcifiants
seabird foraging | la recherche alimentaire des oiseaux marins
marine mammal distribution | la distribution des mammifères marins
~ cetacean acoustic monitoring | le suivi acoustique des cétacés
fish assemblages | les peuplements de poissons
nursery habitats | les habitats de nourricerie
deep-sea ecosystems | les écosystèmes profonds
hydrothermal vent fauna | la faune des sources hydrothermales
biodiversity hotspots | les points chauds de biodiversité
ecosystem functioning | le fonctionnement des écosystèmes
predator-prey dynamics | la dynamique prédateur-proie
keystone species | les espèces clés de voûte
trophic cascades | les cascades trophiques
~ stable isotope ecology | l'écologie des isotopes stables
habitat fragmentation | la fragmentation des habitats
species range shifts | les déplacements d'aires de répartition
phenological shifts | les décalages phénologiques
macroalgal communities | les communautés de macroalgues
epibenthic megafauna | la mégafaune épibenthique
sponge grounds | les champs d'éponges
cold-water corals | les coraux d'eau froide
reef fish behaviour | le comportement des poissons récifaux
~ biodiversity indicators | les indicateurs de biodiversité
~ ecological niche modelling | la modélisation de niche écologique
benthic-pelagic coupling | le couplage benthopélagique
seascape connectivity | la connectivité du paysage marin
ecological restoration | la restauration écologique
seagrass restoration | la restauration des herbiers
mussel beds | les moulières
population genetics | la génétique des populations
genetic connectivity | la connectivité génétique
~ marine biodiversity monitoring | le suivi de la biodiversité marine
~ underwater visual census | les comptages visuels en plongée
~ baited remote underwater video | la vidéo sous-marine appâtée
functional traits | les traits fonctionnels
size spectra | les spectres de taille
coral bleaching | le blanchissement des coraux
artificial reefs | les récifs artificiels
ocean acidification effects | les effets de l'acidification des océans
saltmarsh fauna | la faune des prés salés
"""

_FISHERIES = """
~ stock assessment | l'évaluation des stocks
fishing mortality | la mortalité par pêche
spawning stock biomass | la biomasse du stock reproducteur
catch per unit effort | les captures par unité d'effort
maximum sustainable yield | le rendement maximal durable
bycatch reduction | la réduction des captures accessoires
small-scale fisheries | la pêche artisanale
fishing effort allocation | la répartition de l'effort de pêche
fleet dynamics | la dynamique des flottilles
~ vessel monitoring data | les données de suivi des navires
fishery discards | les rejets de pêche
gear selectivity | la sélectivité des engins de pêche
bottom trawling impacts | les impacts du chalutage de fond
~ age-structured models | les modèles structurés en âge
~ length-based indicators | les indicateurs fondés sur la taille
~ recruitment forecasting | la prévision du recrutement
~ otolith microchemistry | la microchimie des otolithes
~ growth rate estimation | l'estimation des taux de croissance
fisheries management | la gestion des pêches
ecosystem-based fisheries management | la gestion écosystémique des pêches
harvest control rules | les règles de contrôle des captures
total allowable catch | le total admissible des captures
fishing quotas | les quotas de pêche
shellfish aquaculture | la conchyliculture
oyster farming | l'ostréiculture
mussel farming | la mytiliculture
finfish aquaculture | la pisciculture marine
integrated multi-trophic aquaculture | l'aquaculture multitrophique intégrée
offshore aquaculture | l'aquaculture au large
aquaculture carrying capacity | la capacité de charge des bassins conchylicoles
seaweed cultivation | l'algoculture
~ shellfish growth models | les modèles de croissance des coquillages
shellfish mortality outbreaks | les épisodes de mortalité conchylicole
oyster herpesvirus | l'herpèsvirus de l'huître
hatchery production | la production en écloserie
feed conversion efficiency | l'efficacité de conversion alimentaire
fish welfare | le bien-être des poissons
recirculating aquaculture systems | les systèmes aquacoles en recirculation
stock-recruitment relationships | les relations stock-recrutement
fisheries-induced evolution | l'évolution induite par la pêche
size at maturity | la taille à maturité
~ fish condition indices | les indices de condition des poissons
~ acoustic fish surveys | les campagnes acoustiques halieutiques
~ scientific trawl surveys | les campagnes de chalutage scientifique
~ landings data | les données de débarquement
illegal fishing | la pêche illicite
fisheries economics | l'économie des pêches
fishing communities | les communautés de pêcheurs
recreational fisheries | la pêche de loisir
elasmobranch conservation | la conservation des élasmobranches
tuna fisheries | les pêcheries thonières
demersal fish stocks | les stocks démersaux
small pelagic fish | les petits pélagiques
forage fish | les poissons fourrage
fishing gear innovation | l'innovation des engins de pêche
spatial fisheries closures | les fermetures spatiales de pêche
~ shellfish sanitary monitoring | la surveillance sanitaire des coquillages
aquaculture site selection | le choix des sites aquacoles
fish farm benthic impacts | les impacts benthiques des fermes piscicoles
fish stock collapse | l'effondrement des stocks
~ data-limited stock assessment | l'évaluation des stocks pauvres en données
~ Bayesian stock models | les modèles bayésiens de dynamique des stocks
~ management strategy evaluation | l'évaluation des stratégies de gestion
~ fish diet analysis | l'analyse des régimes alimentaires des poissons
nursery grounds | les zones de nourricerie
~ fishing pressure indicators | les indicateurs de pression de pêche
aquaculture feed ingredients | les ingrédients des aliments aquacoles
oyster spat collection | le captage du naissain d'huîtres
mariculture development | le développement de la mariculture
~ catch reconstruction | la reconstruction des captures
recruitment variability | la variabilité du recrutement
"""

_PLANKTON = """
phytoplankton blooms | les efflorescences phytoplanctoniques
harmful algal blooms | les efflorescences algales toxiques
primary production | la production primaire
~ chlorophyll fluorescence | la fluorescence chlorophyllienne
zooplankton grazing | le broutage du zooplancton
copepod population dynamics | la dynamique des populations de copépodes
microbial loop | la boucle microbienne
nutrient limitation | la limitation en nutriments
nitrogen cycling | le cycle de l'azote
phosphorus dynamics | la dynamique du phosphore
silicate depletion | l'épuisement des silicates
carbon export | l'export de carbone
biological carbon pump | la pompe biologique de carbone
dissolved organic matter | la matière organique dissoute
particulate organic carbon | le carbone organique particulaire
carbonate chemistry | la chimie des carbonates
ocean acidification | l'acidification des océans
oxygen minimum zones | les zones de minimum d'oxygène
coastal hypoxia | l'hypoxie côtière
coastal eutrophication | l'eutrophisation côtière
diatom communities | les communautés de diatomées
dinoflagellate cysts | les kystes de dinoflagellés
picophytoplankton abundance | l'abondance du picophytoplancton
phytoplankton phenology | la phénologie du phytoplancton
plankton community structure | la structure des communautés planctoniques
~ plankton imaging | l'imagerie du plancton
~ flow cytometry | la cytométrie en flux
~ pigment analysis | l'analyse pigmentaire
nitrogen fixation | la fixation de l'azote
denitrification rates | les taux de dénitrification
benthic nutrient fluxes | les flux benthiques de nutriments
sediment oxygen demand | la demande en oxygène du sédiment
air-sea CO2 exchange | les échanges de CO2 entre l'océan et l'atmosphère
total alkalinity | l'alcalinité totale
trace metal speciation | la spéciation des métaux traces
iron limitation | la limitation par le fer
marine viruses | les virus marins
bacterioplankton diversity | la diversité du bactérioplancton
mixotrophic protists | les protistes mixotrophes
gelatinous zooplankton | le zooplancton gélatineux
jellyfish outbreaks | les proliférations de méduses
sinking particle flux | le flux de particules sédimentantes
marine snow | la neige marine
~ sediment traps | les pièges à particules
organic matter remineralisation | la reminéralisation de la matière organique
elemental stoichiometry | la stœchiométrie élémentaire
nutrient ratios | les rapports stœchiométriques des nutriments
~ biogeochemical modelling | la modélisation biogéochimique
~ lower trophic level models | les modèles des bas niveaux trophiques
plankton biodiversity | la biodiversité planctonique
phytoplankton size classes | les classes de taille du phytoplancton
~ ocean colour algorithms | les algorithmes de couleur de l'océan
dissolved oxygen dynamics | la dynamique de l'oxygène dissous
nitrous oxide emissions | les émissions de protoxyde d'azote
methane seepage | les suintements de méthane
blue carbon | le carbone bleu
carbon sequestration | la séquestration du carbone
phytoplankton photophysiology | la photophysiologie du phytoplancton
light limitation | la limitation par la lumière
spring bloom timing | la date de l'efflorescence printanière
toxin production | la production de toxines
microzooplankton herbivory | l'herbivorie du microzooplancton
diel vertical migration | la migration verticale nycthémérale
upwelled nutrient supply | les apports de nutriments par upwelling
~ isotopic tracers | les traceurs isotopiques
silica cycle | le cycle de la silice
net community production | la production communautaire nette
carbon budgets | les bilans de carbone
~ plankton time series | les séries temporelles de plancton
coccolithophore calcification | la calcification des coccolithophoridés
benthic-pelagic coupling | le couplage benthopélagique
"""

_HAZARDS = """
sea-level rise | l'élévation du niveau de la mer
coastal flooding | la submersion marine
storm surges | les surcotes de tempête
extreme water levels | les niveaux d'eau extrêmes
compound flooding | les inondations composées
wave overtopping | le franchissement par les vagues
~ coastal vulnerability index | l'indice de vulnérabilité côtière
shoreline retreat | le recul du trait de côte
coastal erosion hazard | l'aléa érosion côtière
~ flood risk mapping | la cartographie du risque d'inondation
early warning systems | les systèmes d'alerte précoce
tsunami inundation | l'inondation par tsunami
~ tsunami hazard assessment | l'évaluation de l'aléa tsunami
~ return period estimation | l'estimation des périodes de retour
~ extreme value analysis | l'analyse des valeurs extrêmes
storm clustering | la succession des tempêtes
wave climate | le climat de houle
extreme wave heights | les hauteurs de vagues extrêmes
coastal defence structures | les ouvrages de défense contre la mer
seawall performance | la performance des murs de protection
dyke breaching | la rupture des digues
managed realignment | la dépoldérisation
nature-based solutions | les solutions fondées sur la nature
coastal adaptation strategies | les stratégies d'adaptation côtière
relative sea-level change | la variation relative du niveau marin
land subsidence | la subsidence des sols
vertical land motion | les mouvements verticaux du sol
glacial isostatic adjustment | l'ajustement isostatique glaciaire
~ sea-level projections | les projections du niveau de la mer
~ probabilistic flood modelling | la modélisation probabiliste des inondations
~ hydrodynamic flood models | les modèles hydrodynamiques de submersion
~ storm impact scale | l'échelle d'impact des tempêtes
post-storm recovery | le rétablissement après tempête
flood exposure | l'exposition aux inondations
coastal risk perception | la perception du risque côtier
saltwater intrusion | l'intrusion saline
groundwater salinisation | la salinisation des nappes
tropical cyclones | les cyclones tropicaux
extratropical storms | les tempêtes extratropicales
meteotsunami events | les épisodes de météotsunami
landslide-generated waves | les vagues générées par glissement de terrain
coastal landslides | les glissements de terrain côtiers
cliff collapse hazard | l'aléa effondrement de falaise
~ flood damage functions | les fonctions de dommages liées aux inondations
flood insurance | l'assurance contre les inondations
hazard zoning | le zonage des aléas
coastal squeeze | le coincement côtier
emergency response planning | la planification des secours
climate change adaptation | l'adaptation au changement climatique
~ multi-hazard assessment | l'évaluation multi-aléas
flood defence failure | la défaillance des ouvrages de défense
wave setup | la surélévation due aux vagues
surge-tide interaction | l'interaction entre marée et surcote
~ historical storm reconstruction | la reconstruction des tempêtes historiques
washover fans | les éventails de débordement
coastal resilience | la résilience côtière
risk governance | la gouvernance des risques
~ flood forecasting | la prévision des submersions
~ digital elevation models | les modèles numériques de terrain
overtopping discharge | le débit de franchissement
levee stability | la stabilité des digues
estuarine flooding | les inondations estuariennes
storm tides | les marées de tempête
erosion hotspots | les points chauds d'érosion
shoreline armouring | l'enrochement du littoral
~ vulnerability mapping | la cartographie de la vulnérabilité
residual risk | le risque résiduel
~ sea-level rise scenarios | les scénarios d'élévation du niveau de la mer
critical infrastructure exposure | l'exposition des infrastructures critiques
adaptation pathways | les trajectoires d'adaptation
planned relocation | la relocalisation planifiée
"""

_OBSERVATION = """
~ satellite altimetry | l'altimétrie satellitaire
~ ocean colour remote sensing | la télédétection de la couleur de l'océan
~ synthetic aperture radar | le radar à synthèse d'ouverture
sea surface salinity | la salinité de surface de la mer
~ high-frequency radar | le radar haute fréquence
~ autonomous underwater vehicles | les véhicules sous-marins autonomes
~ underwater gliders | les planeurs sous-marins
~ profiling floats | les flotteurs profileurs
~ moored instruments | les instruments mouillés
~ acoustic Doppler current profilers | les profileurs de courant à effet Doppler
~ CTD profiles | les profils CTD
~ multibeam echosounder surveys | les levés au sondeur multifaisceau
~ side-scan sonar | le sonar à balayage latéral
~ airborne lidar bathymetry | la bathymétrie lidar aéroportée
~ unmanned aerial vehicles | les drones aériens
~ drone photogrammetry | la photogrammétrie par drone
~ hyperspectral imagery | l'imagerie hyperspectrale
~ multispectral imagery | l'imagerie multispectrale
~ satellite-derived bathymetry | la bathymétrie dérivée des images satellitaires
~ sensor calibration | l'étalonnage des capteurs
~ observing system design | la conception des systèmes d'observation
~ biogeochemical sensors | les capteurs biogéochimiques
~ passive acoustic monitoring | la surveillance acoustique passive
~ underwater acoustics | l'acoustique sous-marine
~ image classification | la classification d'images
~ deep learning segmentation | la segmentation par apprentissage profond
~ gap filling of time series | le comblement des lacunes des séries temporelles
~ cloud masking | le masquage des nuages
~ atmospheric correction | la correction atmosphérique
~ radiometric validation | la validation radiométrique
~ significant wave height retrieval | l'estimation de la hauteur significative des vagues
~ scatterometer winds | les vents mesurés par diffusiomètre
~ surface current mapping | la cartographie des courants de surface
~ sea ice monitoring | le suivi de la glace de mer
~ thermal infrared imagery | l'imagerie infrarouge thermique
~ in situ validation | la validation in situ
~ citizen science observations | les observations de sciences participatives
~ coastal observatories | les observatoires côtiers
~ real-time data transmission | la transmission de données en temps réel
~ biofouling mitigation | la lutte contre les salissures biologiques
~ sensor networks | les réseaux de capteurs
~ data quality control | le contrôle qualité des données
~ open data infrastructures | les infrastructures de données ouvertes
~ metadata standards | les standards de métadonnées
~ Argo floats | les flotteurs Argo
~ water column imaging | l'imagerie de la colonne d'eau
~ seabed classification | la classification des fonds marins
~ acoustic seabed mapping | la cartographie acoustique des fonds
~ optical backscatter sensors | les capteurs de rétrodiffusion optique
~ turbidity monitoring | le suivi de la turbidité
~ shoreline detection algorithms | les algorithmes de détection du trait de côte
~ coastal video systems | les systèmes vidéo côtiers
~ GNSS reflectometry | la réflectométrie GNSS
~ tide gauge networks | les réseaux marégraphiques
~ ship-based surveys | les campagnes océanographiques en mer
~ ocean forecasting systems | les systèmes de prévision océanique
~ ocean digital twins | les jumeaux numériques de l'océan
~ spectral wave buoys | les bouées houlographes
sea surface temperature | la température de surface de la mer
inherent optical properties | les propriétés optiques inhérentes
~ suspended matter retrieval | l'estimation des matières en suspension
~ altimetry waveform retracking | le retraitement des formes d'onde altimétriques
~ wide-swath altimetry | l'altimétrie à large fauchée
radar backscatter | la rétrodiffusion radar
~ change detection | la détection de changements
~ photogrammetric surveys | les levés photogrammétriques
~ terrestrial laser scanning | la lasergrammétrie terrestre
~ uncrewed surface vehicles | les drones de surface
~ habitat mapping | la cartographie des habitats
~ mooring arrays | les réseaux de mouillages
~ data assimilation | l'assimilation de données
"""

_POLLUTION = """
microplastic pollution | la pollution par les microplastiques
microplastic ingestion | l'ingestion de microplastiques
nanoplastic toxicity | la toxicité des nanoplastiques
marine litter | les déchets marins
~ beach litter surveys | les relevés de déchets sur les plages
plastic debris transport | le transport des débris plastiques
~ polymer identification | l'identification des polymères
~ infrared spectroscopy | la spectroscopie infrarouge
persistent organic pollutants | les polluants organiques persistants
polycyclic aromatic hydrocarbons | les hydrocarbures aromatiques polycycliques
oil spills | les marées noires
~ oil spill modelling | la modélisation des marées noires
heavy metal contamination | la contamination par les métaux lourds
mercury bioaccumulation | la bioaccumulation du mercure
trace metal contamination | la contamination par les éléments traces métalliques
emerging contaminants | les contaminants émergents
pharmaceutical residues | les résidus pharmaceutiques
pesticide runoff | le ruissellement de pesticides
antifouling biocides | les biocides antisalissures
organotin compounds | les composés organostanniques
endocrine disruptors | les perturbateurs endocriniens
~ ecotoxicological bioassays | les bioessais écotoxicologiques
~ biomarker responses | les réponses des biomarqueurs
~ sentinel species | les espèces sentinelles
~ mussel watch programmes | les programmes de biosurveillance par les moules
sediment contamination | la contamination des sédiments
dredged sediment management | la gestion des sédiments de dragage
wastewater discharges | les rejets d'eaux usées
faecal contamination | la contamination fécale
bathing water quality | la qualité des eaux de baignade
nutrient pollution | la pollution par les nutriments
agricultural runoff | le ruissellement agricole
plastic additives | les additifs plastiques
textile microfibres | les microfibres textiles
plastic weathering | le vieillissement des plastiques
biofilm colonisation | la colonisation par les biofilms
plastisphere communities | les communautés de la plastisphère
riverine plastic fluxes | les flux fluviaux de plastiques
floating debris accumulation | l'accumulation de débris flottants
underwater noise pollution | la pollution sonore sous-marine
artificial light pollution | la pollution lumineuse
thermal discharges | les rejets thermiques
~ radionuclide tracers | les traceurs radioactifs
~ chemical risk assessment | l'évaluation du risque chimique
~ toxicokinetic modelling | la modélisation toxicocinétique
oxidative stress | le stress oxydant
trophic transfer of contaminants | le transfert trophique des contaminants
~ passive samplers | les échantillonneurs passifs
~ non-target screening | le criblage non ciblé
~ high-resolution mass spectrometry | la spectrométrie de masse à haute résolution
PFAS contamination | la contamination par les PFAS
shipwreck pollution | la pollution liée aux épaves
ghost fishing gear | les engins de pêche fantômes
plastic pollution policy | les politiques de lutte contre la pollution plastique
~ pollution source apportionment | l'attribution des sources de pollution
sewage outfalls | les émissaires d'eaux usées
~ contaminant fate modelling | la modélisation du devenir des contaminants
sediment quality guidelines | les critères de qualité des sédiments
chemical mixtures | les mélanges chimiques
hydrocarbon biodegradation | la biodégradation des hydrocarbures
oil dispersants | les dispersants pétroliers
~ microplastic sampling methods | les méthodes d'échantillonnage des microplastiques
~ neuston nets | les filets à neuston
~ litter drift modelling | la modélisation de la dérive des déchets
environmental quality standards | les normes de qualité environnementale
antibiotic resistance genes | les gènes de résistance aux antibiotiques
pathogenic vibrios | les vibrions pathogènes
~ pollution monitoring networks | les réseaux de surveillance de la pollution
cumulative impacts | les impacts cumulés
harmful algal blooms | les efflorescences algales toxiques
trace metal speciation | la spéciation des métaux traces
underwater radiated noise | le bruit rayonné sous l'eau
"""

_GOVERNANCE = """
integrated coastal zone management | la gestion intégrée des zones côtières
marine spatial planning | la planification de l'espace maritime
coastal governance | la gouvernance côtière
stakeholder participation | la participation des parties prenantes
local ecological knowledge | les savoirs écologiques locaux
community-based management | la gestion communautaire
co-management arrangements | les arrangements de cogestion
environmental justice | la justice environnementale
coastal livelihoods | les moyens de subsistance littoraux
social vulnerability | la vulnérabilité sociale
place attachment | l'attachement au lieu
~ risk perception surveys | les enquêtes de perception du risque
policy instruments | les instruments d'action publique
maritime law | le droit maritime
law of the sea | le droit de la mer
marine protected area governance | la gouvernance des aires marines protégées
social-ecological systems | les systèmes socio-écologiques
adaptive governance | la gouvernance adaptative
collective action | l'action collective
commons management | la gestion des communs
coastal tourism | le tourisme littoral
second-home development | le développement des résidences secondaires
coastal urbanisation | l'urbanisation du littoral
land-use conflicts | les conflits d'usage
planned relocation | la relocalisation planifiée
climate-related migration | les migrations climatiques
heritage landscapes | les paysages patrimoniaux
maritime heritage | le patrimoine maritime
environmental history | l'histoire environnementale
ocean literacy | la culture océanique
science-policy interface | l'interface science-politique
knowledge co-production | la coproduction des savoirs
deliberative democracy | la démocratie délibérative
public consultation | la concertation publique
spatial planning | l'aménagement du territoire
coastal land tenure | le foncier littoral
fishers' knowledge | les savoirs des pêcheurs
customary marine tenure | les régimes fonciers marins coutumiers
environmental conflicts | les conflits environnementaux
social acceptance of offshore wind | l'acceptabilité sociale de l'éolien en mer
media coverage | la couverture médiatique
policy mobilities | la circulation des politiques publiques
transboundary cooperation | la coopération transfrontalière
ocean governance | la gouvernance des océans
blue justice | la justice bleue
adaptation pathways | les trajectoires d'adaptation
~ willingness to pay | le consentement à payer
~ ecosystem services valuation | l'évaluation des services écosystémiques
cultural ecosystem services | les services écosystémiques culturels
environmental education | l'éducation à l'environnement
policy implementation gaps | les écarts de mise en œuvre des politiques
multilevel governance | la gouvernance multiniveau
legal pluralism | le pluralisme juridique
property rights regimes | les régimes de droits de propriété
territorial identity | l'identité territoriale
disaster memory | la mémoire des catastrophes
power relations | les rapports de pouvoir
coastal risk perception | la perception du risque côtier
risk governance | la gouvernance des risques
fishing communities | les communautés de pêcheurs
coastal adaptation strategies | les stratégies d'adaptation côtière
shoreline access rights | l'accès au rivage
coastal gentrification | la gentrification littorale
island communities | les communautés insulaires
fisheries governance | la gouvernance des pêches
marine conservation conflicts | les conflits liés à la conservation marine
coastal planning law | le droit de l'urbanisme littoral
community resilience | la résilience des communautés
tourism carrying capacity | la capacité de charge touristique
maritime boundaries | les frontières maritimes
"""

_BLUE_ECONOMY = """
port logistics | la logistique portuaire
container shipping | le transport maritime conteneurisé
port competitiveness | la compétitivité portuaire
hinterland connectivity | la desserte de l'arrière-pays
maritime transport emissions | les émissions du transport maritime
shipping decarbonisation | la décarbonation du transport maritime
alternative marine fuels | les carburants marins alternatifs
ballast water management | la gestion des eaux de ballast
port expansion | l'extension portuaire
dredging operations | les opérations de dragage
navigation channels | les chenaux de navigation
ship traffic density | la densité du trafic maritime
~ AIS data analysis | l'analyse des données AIS
maritime safety | la sécurité maritime
offshore wind energy | l'énergie éolienne en mer
floating wind turbines | les éoliennes flottantes
tidal stream energy | l'énergie hydrolienne
wave energy converters | les systèmes houlomoteurs
marine renewable energy | les énergies marines renouvelables
offshore wind farm impacts | les impacts des parcs éoliens en mer
subsea cables | les câbles sous-marins
deep-sea mining | l'exploitation minière des grands fonds
marine biotechnology | la biotechnologie marine
blue growth strategies | les stratégies de croissance bleue
maritime clusters | les clusters maritimes
shipbuilding industry | l'industrie de la construction navale
cruise tourism | le tourisme de croisière
port-city relations | les relations ville-port
waterfront redevelopment | la reconversion des fronts d'eau
port governance | la gouvernance portuaire
maritime spatial conflicts | les conflits d'usage en mer
~ ocean economy accounts | les comptes de l'économie maritime
~ input-output analysis | l'analyse entrées-sorties
~ cost-benefit analysis | l'analyse coûts-avantages
~ natural capital accounting | la comptabilité du capital naturel
seaweed value chains | les filières des algues
port state control | le contrôle par l'État du port
shipping routes | les routes maritimes
Arctic shipping | la navigation arctique
vessel speed reduction | la réduction de la vitesse des navires
ship strikes on whales | les collisions entre navires et cétacés
port water quality | la qualité des eaux portuaires
maritime labour | le travail maritime
seafarer welfare | le bien-être des marins
freight flows | les flux de fret
short sea shipping | le transport maritime à courte distance
intermodal transport | le transport intermodal
port resilience | la résilience portuaire
port climate adaptation | l'adaptation des ports au changement climatique
offshore decommissioning | le démantèlement des installations en mer
coastal tourism economics | l'économie du tourisme littoral
recreational boating | la navigation de plaisance
marina development | l'aménagement des ports de plaisance
ocean energy policy | les politiques énergétiques maritimes
~ environmental impact assessment | l'étude d'impact environnemental
hull biofouling | les salissures biologiques des coques
green port initiatives | les démarches de ports verts
shore power | l'alimentation électrique à quai
liquefied natural gas bunkering | le soutage au gaz naturel liquéfié
supply chain disruptions | les perturbations des chaînes d'approvisionnement
maritime economics | l'économie maritime
maritime employment | l'emploi maritime
port authority strategies | les stratégies des autorités portuaires
offshore wind supply chain | la chaîne d'approvisionnement de l'éolien en mer
~ blue economy indicators | les indicateurs de l'économie bleue
fisheries economics | l'économie des pêches
coastal tourism | le tourisme littoral
~ willingness to pay | le consentement à payer
~ ecosystem services valuation | l'évaluation des services écosystémiques
underwater radiated noise | le bruit rayonné sous l'eau
marine aggregate extraction | l'extraction de granulats marins
artificial reefs | les récifs artificiels
"""

_ESTUARIES = """
salt marsh accretion | l'accrétion des marais salés
tidal marsh vegetation | la végétation des marais tidaux
mangrove forests | les forêts de mangroves
mangrove carbon stocks | les stocks de carbone des mangroves
estuarine turbidity maximum | le bouchon vaseux
salt wedge dynamics | la dynamique du coin salé
mudflat erosion | l'érosion des vasières
fine sediment trapping | le piégeage des sédiments fins
marsh edge erosion | l'érosion des bordures de marais
tidal channel networks | les réseaux de chenaux de marée
estuarine morphodynamics | la morphodynamique estuarienne
river discharge variability | la variabilité des débits fluviaux
wetland restoration | la restauration des zones humides
coastal lagoons | les lagunes côtières
lagoon inlet stability | la stabilité des graus
brackish water ecology | l'écologie des eaux saumâtres
halophyte communities | les communautés d'halophytes
sediment accretion rates | les taux d'accrétion sédimentaire
marsh elevation dynamics | la dynamique altimétrique des marais
~ surface elevation tables | les tables d'élévation de surface
carbon burial | l'enfouissement du carbone
wetland biogeochemistry | la biogéochimie des zones humides
estuarine fish nurseries | les nourriceries estuariennes
fish passage restoration | le rétablissement de la continuité écologique
diadromous fish | les poissons amphihalins
eel migration | la migration des anguilles
waterbird habitats | les habitats des oiseaux d'eau
shorebird feeding grounds | les zones d'alimentation des limicoles
polder management | la gestion des polders
tidal reconnection | la reconnexion tidale
estuarine water quality | la qualité des eaux estuariennes
estuarine hypoxia | l'hypoxie estuarienne
mangrove dieback | le dépérissement des mangroves
reed beds | les roselières
wetland hydrology | l'hydrologie des zones humides
groundwater-surface water exchange | les échanges entre nappe et eaux de surface
estuarine sediment budgets | les bilans sédimentaires estuariens
tidal wave propagation | la propagation de l'onde de marée
tidal bore | le mascaret
haline stratification | la stratification haline
fluid mud layers | les couches de crème de vase
estuarine ecosystem health | la santé des écosystèmes estuariens
wetland loss | la disparition des zones humides
landward marsh migration | la migration des marais vers l'intérieur des terres
marsh resilience to sea-level rise | la résilience des marais face à l'élévation du niveau marin
mangrove propagule dispersal | la dispersion des propagules de mangrove
bivalve filtration | la filtration par les bivalves
estuarine food webs | les réseaux trophiques estuariens
nutrient retention | la rétention des nutriments
wetland ecosystem services | les services écosystémiques des zones humides
river-estuary continuum | le continuum fleuve-estuaire
sediment starvation | le déficit sédimentaire
delta subsidence | la subsidence des deltas
deltaic wetlands | les zones humides deltaïques
salt pans | les marais salants
tidal freshwater marshes | les marais d'eau douce soumis à la marée
~ wetland mapping | la cartographie des zones humides
vegetation-flow interactions | les interactions entre végétation et écoulement
wave attenuation by vegetation | l'atténuation de la houle par la végétation
mangrove restoration | la restauration des mangroves
lagoon eutrophication | l'eutrophisation lagunaire
macrotidal estuaries | les estuaires macrotidaux
microtidal lagoons | les lagunes microtidales
estuarine mixing | le mélange estuarien
estuarine circulation | la circulation estuarienne
blue carbon ecosystems | les écosystèmes de carbone bleu
saltmarsh grazing | le pâturage des prés salés
saltwater intrusion | l'intrusion saline
water residence time | le temps de résidence des eaux
nursery habitats | les habitats de nourricerie
saltmarsh fauna | la faune des prés salés
"""

_PALEO = """
~ sediment cores | les carottes sédimentaires
foraminiferal assemblages | les assemblages de foraminifères
planktonic foraminifera | les foraminifères planctoniques
benthic foraminifera | les foraminifères benthiques
~ oxygen isotope stratigraphy | la stratigraphie isotopique de l'oxygène
~ radiocarbon dating | la datation par le radiocarbone
~ age-depth models | les modèles âge-profondeur
Holocene climate variability | la variabilité climatique holocène
Last Glacial Maximum | le dernier maximum glaciaire
deglacial sea-level history | l'histoire du niveau marin à la déglaciation
relative sea-level reconstruction | la reconstruction du niveau marin relatif
sea surface temperature reconstruction | la reconstruction des températures de surface
~ alkenone palaeothermometry | la paléothermométrie des alcénones
~ magnesium-calcium thermometry | la thermométrie magnésium-calcium
~ diatom-based transfer functions | les fonctions de transfert fondées sur les diatomées
marine pollen records | les enregistrements polliniques marins
coral climate archives | les archives climatiques coralliennes
~ bivalve sclerochronology | la sclérochronologie des bivalves
~ marine tephrochronology | la téphrochronologie marine
marine isotope stages | les stades isotopiques marins
ocean ventilation changes | les changements de ventilation océanique
~ past circulation proxies | les traceurs de la circulation passée
~ neodymium isotopes | les isotopes du néodyme
ice-rafted debris | les débris transportés par les glaces
abrupt climate events | les événements climatiques abrupts
Heinrich events | les événements de Heinrich
millennial-scale variability | la variabilité millénaire
orbital forcing | le forçage orbital
~ paleoproductivity proxies | les indicateurs de paléoproductivité
biogenic silica | la silice biogène
sediment geochemistry | la géochimie sédimentaire
~ X-ray fluorescence core scanning | l'analyse des carottes par fluorescence X
varved sediments | les sédiments varvés
paleostorm records | les archives de paléotempêtes
paleotsunami deposits | les dépôts de paléotsunamis
coastal barrier evolution | l'évolution des barrières côtières
Holocene sea-level highstand | le haut niveau marin holocène
submerged landscapes | les paysages submergés
~ seismic stratigraphy | la stratigraphie sismique
continental margin sedimentation | la sédimentation sur les marges continentales
turbidite records | les enregistrements de turbidites
contourite drifts | les dépôts contouritiques
glacial-interglacial cycles | les cycles glaciaires-interglaciaires
carbon cycle feedbacks | les rétroactions du cycle du carbone
deep water formation | la formation des eaux profondes
~ proxy calibration | l'étalonnage des traceurs paléoclimatiques
~ model-data comparison | la comparaison entre modèles et données
~ paleoclimate modelling | la modélisation paléoclimatique
~ Bayesian age modelling | la modélisation bayésienne des âges
~ lipid biomarkers | les biomarqueurs lipidiques
~ GDGT-based palaeothermometry | la paléothermométrie fondée sur les GDGT
dinocyst assemblages | les assemblages de dinokystes
coccolith records | les enregistrements de coccolithes
sediment provenance | la provenance des sédiments
palaeoenvironmental reconstruction | la reconstruction paléoenvironnementale
estuarine infilling history | l'histoire du comblement estuarien
Little Ice Age | le petit âge glaciaire
Medieval Climate Anomaly | l'anomalie climatique médiévale
monsoon variability | la variabilité de la mousson
ENSO variability | la variabilité ENSO
sea ice reconstruction | la reconstruction de la glace de mer
marine terraces | les terrasses marines
~ uranium-series dating | la datation par les séries de l'uranium
~ optically stimulated luminescence | la luminescence stimulée optiquement
beachrock formation | la formation des grès de plage
shell middens | les amas coquilliers
coastal geoarchaeology | la géoarchéologie littorale
deep-sea coral geochemistry | la géochimie des coraux profonds
~ palaeoceanographic proxies | les indicateurs paléocéanographiques
dinoflagellate cysts | les kystes de dinoflagellés
glacial isostatic adjustment | l'ajustement isostatique glaciaire
"""

# ---------------------------------------------------------------------------
# Compound terms: families of aspects and objects
# ---------------------------------------------------------------------------
# Real subfields name many specific things: a species and one of its traits,
# an instrument and a processing step. Each family below lists aspects, then
# "--", then objects (English modifier | French with article); every pair
# gives one term, English "<object> <aspect>" and French "<aspect> de
# <object>" (``hake biomass``, ``la biomasse du merlu``). "==" separates
# families; a leading "~" on an aspect makes its terms techniques.

_GEOMORPHOLOGY_FAMILIES = """
erosion | l'érosion
morphology | la morphologie
evolution | l'évolution
stability | la stabilité
sediment budget | le bilan sédimentaire
volume changes | les variations de volume
~ monitoring | le suivi
~ mapping | la cartographie
vulnerability | la vulnérabilité
resilience | la résilience
dynamics | la dynamique
topography | la topographie
sediment supply | les apports sédimentaires
~ surveys | les levés
~ modelling | la modélisation
--
beach | les plages
dune | les dunes
cliff | les falaises
barrier | les cordons littoraux
spit | les flèches sableuses
sandbar | les barres sableuses
shoreface | l'avant-côte
foredune | les avant-dunes
embayment | les anses
tombolo | les tombolos
shingle beach | les plages de galets
chenier | les cheniers
coastal plain | les plaines côtières
berm | les bermes
nearshore bar | les barres d'avant-côte
tidal delta | les deltas de marée
beach ridge plain | les plaines de cordons
barrier island | les îles-barrières
pocket beach | les plages de poche
coastal dune field | les champs de dunes littoraux
rocky shore | les estrans rocheux
headland | les caps
delta front | le front de delta
sand bank | les bancs de sable
upper beach | les hauts de plage
dune slack | les pannes dunaires
gravel spit | les flèches de galets
==
grain size | la granulométrie
mineralogy | la minéralogie
~ sampling | l'échantillonnage
sorting | le tri
provenance | la provenance
~ tracing | le traçage
--
beach sand | les sables de plage
dune sand | les sables dunaires
gravel | les galets
shelf sediment | les sédiments du plateau
mud | les vases
tidal flat sediment | les sédiments d'estran
river sediment | les sédiments fluviaux
aeolian sand | les sables éoliens
shell hash | les débris coquilliers
carbonate sand | les sables carbonatés
==
bedforms | les figures sédimentaires
roughness | la rugosité
permeability | la perméabilité
infiltration | l'infiltration
compaction | la compaction
--
swash zone | la zone de jet de rive
beach step | la marche de plage
gravel berm | les bermes de galets
intertidal bar | les barres intertidales
runnel | les bâches
backshore | l'arrière-plage
"""

_CIRCULATION_FAMILIES = """
variability | la variabilité
seasonal cycle | le cycle saisonnier
vertical structure | la structure verticale
energetics | l'énergétique
forcing mechanisms | les mécanismes de forçage
~ modelling | la modélisation
dynamics | la dynamique
intensity | l'intensité
persistence | la persistance
transport | le transport
~ observations | les observations
~ parameterisation | la paramétrisation
--
shelf current | les courants de plateau
slope current | les courants de pente
tidal current | les courants de marée
coastal current | les courants côtiers
river plume | les panaches fluviaux
upwelling | l'upwelling
eddy | les tourbillons
internal wave | les ondes internes
mixed layer | la couche de mélange
front | les fronts
boundary current | les courants de bord
bottom boundary layer | la couche limite de fond
thermocline | la thermocline
halocline | l'halocline
shelf break front | le front de talus
tidal jet | les jets de marée
wind-driven current | les courants de dérive
overflow | les débordements d'eau profonde
recirculation gyre | les gyres de recirculation
trapped wave | les ondes piégées
surface current | les courants de surface
undercurrent | les sous-courants
plume front | les fronts de panache
tidal inlet | les passes tidales
Rossby wave | les ondes de Rossby
cold pool | les masses d'eau froide de fond
shelf break | le rebord du plateau
near-inertial wave | les ondes quasi inertielles
Ekman layer | la couche d'Ekman
bottom current | les courants de fond
==
heat budget | le bilan de chaleur
salt budget | le bilan de sel
~ mooring observations | les observations par mouillage
exchange flow | les échanges
flushing | le renouvellement des eaux
stratification | la stratification
--
bay | les baies
fjord | les fjords
shelf sea | les mers de plateau
marginal sea | les mers bordières
strait | les détroits
gulf | les golfes
==
shear | le cisaillement
vorticity | la vorticité
dissipation | la dissipation
entrainment | l'entraînement
~ tracking | le suivi
--
density current | les courants de densité
gravity current | les courants de gravité
cross-shelf flow | les écoulements transversaux
sill overflow | les débordements de seuil
tidal eddy | les tourbillons de marée
headland eddy | les tourbillons de cap
"""

_ECOLOGY_FAMILIES = """
distribution | la distribution
abundance | l'abondance
recruitment | le recrutement
growth | la croissance
feeding ecology | l'écologie trophique
habitat use | l'utilisation de l'habitat
~ monitoring | le suivi
behaviour | le comportement
physiology | la physiologie
reproduction | la reproduction
diet | le régime alimentaire
mortality | la mortalité
thermal tolerance | la tolérance thermique
~ tagging | le marquage
--
mussel | les moules
sea urchin | les oursins
seabird | les oiseaux marins
harbour seal | les phoques veaux-marins
reef fish | les poissons récifaux
polychaete | les polychètes
amphipod | les amphipodes
limpet | les patelles
sea star | les étoiles de mer
crab | les crabes
shrimp | les crevettes
cuttlefish | les seiches
sea turtle | les tortues marines
dolphin | les dauphins
shorebird | les limicoles
juvenile fish | les poissons juvéniles
bivalve | les bivalves
gastropod | les gastéropodes
sea anemone | les anémones de mer
brittle star | les ophiures
barnacle | les balanes
grey seal | les phoques gris
sandeel | les lançons
cephalopod | les céphalopodes
octopus | les poulpes
holothurian | les holothuries
hermit crab | les bernard-l'ermite
seahorse | les hippocampes
sea squirt | les ascidies
tube worm | les vers tubicoles
whale | les baleines
porpoise | les marsouins
tern | les sternes
cormorant | les cormorans
==
cover | le recouvrement
productivity | la productivité
fragmentation | la fragmentation
~ mapping | la cartographie
restoration | la restauration
resilience | la résilience
decline | le déclin
~ monitoring | le suivi
--
seagrass | les herbiers
kelp | les laminaires
coral | les coraux
maerl bed | les bancs de maërl
coralligenous reef | le coralligène
rhodolith bed | les bancs de rhodolithes
macroalgal canopy | la canopée de macroalgues
turf algae | les gazons algaux
==
breeding success | le succès reproducteur
foraging range | le rayon d'alimentation
colony size | la taille des colonies
migration timing | la phénologie migratoire
diving behaviour | le comportement de plongée
--
puffin | les macareux
gannet | les fous de Bassan
kittiwake | les mouettes tridactyles
shearwater | les puffins
fur seal | les otaries à fourrure
guillemot | les guillemots
"""

_FISHERIES_FAMILIES = """
biomass | la biomasse
landings | les débarquements
recruitment | le recrutement
spatial distribution | la distribution spatiale
growth | la croissance
exploitation status | l'état d'exploitation
~ abundance indices | les indices d'abondance
catch | les captures
size structure | la structure en taille
maturity | la maturité
diet | le régime alimentaire
discards | les rejets
~ tagging | le marquage
--
cod | le cabillaud
hake | le merlu
sole | la sole
anchovy | l'anchois
sardine | la sardine
mackerel | le maquereau
sea bass | le bar
plaice | la plie
Norway lobster | la langoustine
tuna | le thon
red mullet | le rouget
whiting | le merlan
monkfish | la baudroie
herring | le hareng
squid | le calmar
sprat | le sprat
pollack | le lieu jaune
horse mackerel | le chinchard
spider crab | l'araignée de mer
brown crab | le tourteau
common cuttlefish | la seiche commune
skate | les raies
black sea bream | la dorade grise
blue whiting | le merlan bleu
John Dory | le saint-pierre
saithe | le lieu noir
haddock | l'églefin
European eel | l'anguille européenne
swordfish | l'espadon
albacore | le germon
==
mortality | la mortalité
growth performance | les performances de croissance
disease resistance | la résistance aux maladies
welfare | le bien-être
feeding | l'alimentation
~ selective breeding | la sélection génétique
--
Pacific oyster | l'huître creuse
blue mussel | la moule commune
gilthead sea bream | la dorade royale
abalone | l'ormeau
sea cucumber | l'holothurie
Atlantic salmon | le saumon atlantique
turbot | le turbot
flat oyster | l'huître plate
clam | la palourde
meagre | le maigre
==
catchability | la capturabilité
selectivity | la sélectivité
bycatch rates | les taux de captures accessoires
fuel efficiency | l'efficacité énergétique
seabed contact | le contact avec le fond
--
otter trawl | les chaluts à panneaux
beam trawl | les chaluts à perche
gillnet | les filets maillants
longline | les palangres
purse seine | les sennes coulissantes
pot fishery | les pêcheries aux casiers
"""

_PLANKTON_FAMILIES = """
abundance | l'abondance
biomass | la biomasse
growth rates | les taux de croissance
grazing losses | les pertes par broutage
seasonal succession | la succession saisonnière
~ time series | les séries temporelles
size structure | la structure en taille
diversity | la diversité
respiration | la respiration
vertical distribution | la distribution verticale
~ counts | les dénombrements
--
diatom | les diatomées
dinoflagellate | les dinoflagellés
coccolithophore | les coccolithophoridés
copepod | les copépodes
cyanobacteria | les cyanobactéries
ciliate | les ciliés
krill | le krill
appendicularian | les appendiculaires
picoeukaryote | les picoeucaryotes
radiolarian | les radiolaires
heterotrophic bacteria | les bactéries hétérotrophes
nanoflagellate | les nanoflagellés
salp | les salpes
pteropod | les ptéropodes
mysid | les mysidacés
rotifer | les rotifères
haptophyte | les haptophytes
cryptophyte | les cryptophytes
Pseudo-nitzschia | les Pseudo-nitzschia
Alexandrium | les Alexandrium
Chaetoceros | les Chaetoceros
Skeletonema | les Skeletonema
Dinophysis | les Dinophysis
Calanus | les Calanus
Oithona | les Oithona
Synechococcus | les Synechococcus
Prochlorococcus | les Prochlorococcus
Emiliania huxleyi | les Emiliania huxleyi
==
uptake | l'assimilation
distribution | la distribution
budget | le bilan
~ measurements | les mesures
cycling | le cycle
fluxes | les flux
limitation | la limitation
--
nitrate | les nitrates
ammonium | l'ammonium
phosphate | les phosphates
silicic acid | l'acide silicique
dissolved iron | le fer dissous
dissolved inorganic carbon | le carbone inorganique dissous
organic nitrogen | l'azote organique
urea | l'urée
nitrite | les nitrites
dissolved organic phosphorus | le phosphore organique dissous
cobalt | le cobalt
manganese | le manganèse
==
sinking velocity | la vitesse de chute
aggregation | l'agrégation
degradation rates | les taux de dégradation
carbon content | la teneur en carbone
~ imaging | l'imagerie
--
faecal pellet | les pelotes fécales
phytodetritus | les phytodétritus
transparent exopolymer | les exopolymères transparents
diatom aggregate | les agrégats de diatomées
zooplankton carcass | les carcasses de zooplancton
mineral ballast | le lest minéral
"""

_HAZARDS_FAMILIES = """
exposure | l'exposition
vulnerability | la vulnérabilité
damage | l'endommagement
adaptation | l'adaptation
relocation | la relocalisation
~ vulnerability assessment | le diagnostic de vulnérabilité
resilience | la résilience
insurance | l'assurance
protection | la protection
flood risk | le risque de submersion
~ damage assessment | l'évaluation des dommages
--
coastal housing | les habitations littorales
port infrastructure | les infrastructures portuaires
coastal road | les routes côtières
campsite | les campings
coastal farmland | les terres agricoles littorales
tourist resort | les stations balnéaires
coastal wastewater plant | les stations d'épuration littorales
coastal airport | les aéroports littoraux
heritage site | les sites patrimoniaux
harbour town | les villes portuaires
coastal railway | les voies ferrées littorales
drinking water supply | l'alimentation en eau potable
coastal hospital | les hôpitaux littoraux
energy network | les réseaux d'énergie
coastal village | les villages littoraux
mobile home park | les parcs résidentiels de loisirs
fish farm | les fermes aquacoles
industrial zone | les zones industrielles littorales
seaside promenade | les promenades de front de mer
marina | les ports de plaisance
grazed salt marsh | les prés salés pâturés
==
return levels | les niveaux de retour
frequency | la fréquence
intensity | l'intensité
trends | les tendances
~ hindcast | la simulation rétrospective
impacts | les impacts
~ forecasting | la prévision
--
storm surge | les surcotes
extreme wave | les vagues extrêmes
coastal flood | les submersions marines
river flood | les crues
storm | les tempêtes
tsunami | les tsunamis
extreme rainfall | les pluies extrêmes
coastal erosion event | les épisodes d'érosion
compound event | les événements composés
high tide flood | les inondations de marée haute
swell event | les épisodes de houle
==
failure modes | les modes de rupture
design standards | les normes de conception
maintenance costs | les coûts d'entretien
crest level | la cote d'arase
~ inspection | l'inspection
--
rubble mound | les digues à talus
sea dyke | les digues maritimes
groyne | les épis
breakwater | les brise-lames
tidal barrier | les barrages anti-marée
flood gate | les portes à flot
"""

_OBSERVATION_FAMILIES = """
~ calibration | l'étalonnage
~ validation | la validation
~ data processing | le traitement des données
~ deployment | le déploiement
~ uncertainty | les incertitudes
~ maintenance | la maintenance
~ intercomparison | l'intercomparaison
~ data quality | la qualité des données
~ sampling strategy | la stratégie d'échantillonnage
~ energy autonomy | l'autonomie énergétique
--
glider | les planeurs
Argo float | les flotteurs Argo
HF radar | les radars HF
ADCP | les courantomètres ADCP
satellite altimeter | les altimètres satellitaires
tide gauge | les marégraphes
wave buoy | les bouées houlographes
lidar | le lidar
multibeam echosounder | les sondeurs multifaisceaux
CTD sensor | les capteurs CTD
underwater camera | les caméras sous-marines
hydrophone | les hydrophones
drifter | les bouées dérivantes
echosounder | les échosondeurs
moored buoy | les bouées ancrées
ocean colour sensor | les capteurs de couleur de l'océan
turbidity sensor | les turbidimètres
oxygen optode | les optodes à oxygène
pH sensor | les capteurs de pH
scatterometer | les diffusiomètres
drone camera | les caméras de drones
fluorometer | les fluorimètres
radiometer | les radiomètres
water sampler | les préleveurs d'eau
benthic lander | les stations benthiques autonomes
current meter | les courantomètres
thermistor chain | les chaînes de thermistances
acoustic release | les largueurs acoustiques
gravity corer | les carottiers par gravité
Niskin bottle | les bouteilles Niskin
sonar | les sonars
towed camera | les caméras tractées
surface buoy | les bouées de surface
wave glider | les planeurs de surface
==
~ gridding | le maillage
~ interpolation | l'interpolation
~ gap filling | le comblement des lacunes
~ downscaling | la descente d'échelle
~ archiving | l'archivage
--
altimetry product | les produits altimétriques
ocean colour product | les produits de couleur de l'océan
reanalysis field | les champs de réanalyse
glider transect | les sections de planeurs
mooring record | les séries de mouillage
radar current map | les cartes de courants radar
"""

_POLLUTION_FAMILIES = """
toxicity | la toxicité
bioavailability | la biodisponibilité
environmental fate | le devenir environnemental
sources | les sources
~ analysis | le dosage
bioaccumulation | la bioaccumulation
degradation | la dégradation
exposure | l'exposition
~ monitoring | la surveillance
~ risk assessment | l'évaluation des risques
--
copper | le cuivre
cadmium | le cadmium
lead | le plomb
zinc | le zinc
arsenic | l'arsenic
PCB | les PCB
glyphosate | le glyphosate
tyre particle | les particules de pneus
flame retardant | les retardateurs de flamme
UV filter | les filtres UV
mercury | le mercure
nickel | le nickel
chromium | le chrome
silver nanoparticle | les nanoparticules d'argent
triclosan | le triclosan
bisphenol A | le bisphénol A
phthalate | les phtalates
diclofenac | le diclofénac
microplastic fibre | les fibres microplastiques
caffeine | la caféine
antibiotic | les antibiotiques
hydrocarbon | les hydrocarbures
tributyltin | le tributylétain
PFOS | le PFOS
dioxin | les dioxines
carbamazepine | la carbamazépine
polystyrene | le polystyrène
brominated compound | les composés bromés
rare earth element | les terres rares
radiocaesium | le césium radioactif
==
contamination | la contamination
~ biomonitoring | la biosurveillance
~ sampling | l'échantillonnage
--
oyster | les huîtres
mussel | les moules
fish muscle | la chair des poissons
seabird egg | les œufs d'oiseaux marins
harbour sediment | les sédiments portuaires
beach sand | les sables de plage
sea turtle | les tortues marines
marine mammal | les mammifères marins
zooplankton | le zooplancton
seagrass | les herbiers
cockle | les coques
flatfish | les poissons plats
seafood | les produits de la mer
estuarine sediment | les sédiments estuariens
==
genotoxicity | la génotoxicité
neurotoxicity | la neurotoxicité
immunotoxicity | l'immunotoxicité
reproductive effects | les effets sur la reproduction
gene expression | l'expression génique
--
oyster larvae | les larves d'huîtres
sea urchin embryo | les embryons d'oursins
copepod nauplii | les nauplii de copépodes
fish embryo | les embryons de poissons
mussel haemocyte | les hémocytes de moules
microalgal culture | les cultures de microalgues
"""

_GOVERNANCE_FAMILIES = """
governance | la gouvernance
regulation | la réglementation
use conflicts | les conflits d'usage
planning | la planification
perceptions | les perceptions
~ stakeholder mapping | la cartographie des acteurs
management | la gestion
valuation | la valorisation
access rights | les droits d'accès
social uses | les usages sociaux
--
shellfish farming | la conchyliculture
beach | les plages
marine reserve | les réserves marines
estuary | les estuaires
coastal wetland | les zones humides littorales
island | les îles
fishing ground | les zones de pêche
seaside town | les villes balnéaires
harbour | les ports
dune system | les systèmes dunaires
bay | les baies
lagoon | les lagunes
offshore wind zone | les zones d'éolien en mer
coastal path | les sentiers littoraux
mooring area | les zones de mouillage
salt marsh | les prés salés
river mouth | les embouchures
marine park | les parcs marins
coastal commune | les communes littorales
protected coastline | les littoraux protégés
oyster basin | les bassins ostréicoles
coastal forest | les forêts littorales
offshore island | les îles du large
tourist beach | les plages touristiques
wild coast | les côtes sauvages
==
history | l'histoire
heritage | la patrimonialisation
memory | la mémoire
tourism | la mise en tourisme
representations | les représentations
--
fishing port | les ports de pêche
lighthouse | les phares
seaside resort | les stations balnéaires
salt pan | les salines
oyster farming | l'ostréiculture
shipwreck | les naufrages
harbour district | les quartiers portuaires
fishing village | les villages de pêcheurs
coastal fortification | les fortifications littorales
sea bathing | les bains de mer
sea rescue | le sauvetage en mer
naval dockyard | les arsenaux
==
access | l'accès
privatisation | la privatisation
commodification | la marchandisation
--
shoreline | le rivage
seafront | le front de mer
sea view | la vue sur mer
coastal land | le foncier littoral
==
legal status | le statut juridique
enforcement | l'application
zoning | le zonage
compensation schemes | les mécanismes de compensation
public inquiry | l'enquête publique
--
public maritime domain | le domaine public maritime
coastal setback | la bande littorale inconstructible
fishing licence | les licences de pêche
mooring concession | les concessions de mouillage
beach concession | les concessions de plage
aquaculture lease | les concessions conchylicoles
"""

_BLUE_ECONOMY_FAMILIES = """
economics | l'économie
market | le marché
value chain | la chaîne de valeur
employment | l'emploi
innovation | l'innovation
~ cost assessment | l'évaluation des coûts
regulation | la régulation
competitiveness | la compétitivité
public funding | le financement public
sustainability | la durabilité
~ economic modelling | la modélisation économique
--
offshore wind | l'éolien en mer
tidal energy | l'énergie marémotrice
seaweed farming | l'algoculture
container port | les ports à conteneurs
cruise industry | la croisière
ferry service | les services de ferry
shipyard | les chantiers navals
fishing fleet | les flottilles de pêche
marina | les ports de plaisance
wave energy | l'énergie houlomotrice
marine aquaculture | l'aquaculture marine
seafood processing | la transformation des produits de la mer
yachting | la grande plaisance
offshore oil | le pétrole en mer
dredging industry | l'industrie du dragage
ship recycling | le recyclage des navires
marine tourism | le tourisme maritime
salt production | la production de sel
ship repair | la réparation navale
marine insurance | l'assurance maritime
seaweed biorefinery | le bioraffinage des algues
fish auction | les criées
shipping line | les compagnies maritimes
coastal hotel | l'hôtellerie littorale
water sports | les sports nautiques
marine equipment | les équipements maritimes
==
emissions | les émissions
traffic | le trafic
~ monitoring | la surveillance
safety | la sécurité
fuel consumption | la consommation de carburant
--
cruise ship | les navires de croisière
container ship | les porte-conteneurs
tanker | les pétroliers
ferry | les ferries
fishing vessel | les navires de pêche
bulk carrier | les vraquiers
LNG carrier | les méthaniers
offshore supply vessel | les navires de servitude
car carrier | les transporteurs de véhicules
research vessel | les navires océanographiques
tugboat | les remorqueurs
==
throughput | le débit
automation | l'automatisation
congestion | la congestion
energy use | la consommation d'énergie
labour relations | les relations de travail
--
container terminal | les terminaux à conteneurs
ro-ro terminal | les terminaux rouliers
bulk terminal | les terminaux vraquiers
dry port | les ports secs
cruise terminal | les terminaux de croisière
ferry terminal | les gares maritimes
"""

_ESTUARIES_FAMILIES = """
sedimentation | la sédimentation
hydrodynamics | l'hydrodynamique
vegetation | la végétation
ecology | l'écologie
restoration | la restauration
morphology | la morphologie
~ monitoring | le suivi
biogeochemistry | la biogéochimie
carbon storage | le stockage de carbone
biodiversity | la biodiversité
water quality | la qualité de l'eau
~ mapping | la cartographie
--
salt marsh | les prés salés
mudflat | les vasières
tidal channel | les chenaux de marée
mangrove | les mangroves
lagoon | les lagunes
reed bed | les roselières
tidal flat | les estrans
polder | les polders
estuary mouth | les embouchures
backbarrier marsh | les marais d'arrière-cordon
coastal pond | les étangs littoraux
freshwater marsh | les marais doux
delta plain | les plaines deltaïques
tidal creek | les chenaux secondaires
sandflat | les estrans sableux
salt meadow | les herbus
brackish marsh | les marais saumâtres
floodplain | les plaines d'inondation
coastal wetland | les zones humides littorales
marsh creek | les chenaux de marais
estuarine beach | les plages estuariennes
tidal wetland | les zones humides tidales
mangrove creek | les chenaux de mangrove
brackish lagoon | les lagunes saumâtres
==
salinity | la salinité
turbidity | la turbidité
nutrient loads | les apports de nutriments
~ water sampling | les prélèvements d'eau
residence time | le temps de résidence
oxygen levels | l'oxygénation
--
estuary | les estuaires
lagoon | les lagunes
delta | les deltas
tidal river | les fleuves soumis à la marée
coastal bay | les baies côtières
==
root biomass | la biomasse racinaire
above-ground productivity | la productivité aérienne
decomposition | la décomposition
zonation | la zonation
invasion | l'invasion
--
Spartina | les spartines
Salicornia | les salicornes
sea purslane | l'obione
reed | les roseaux
rush | les joncs
sedge | les laîches
"""

_PALEO_FAMILIES = """
records | les enregistrements
stratigraphy | la stratigraphie
~ dating | la datation
~ geochemistry | la géochimie
~ sampling | l'échantillonnage
chronology | la chronologie
palaeoecology | la paléoécologie
~ radiocarbon ages | les âges radiocarbone
~ isotope analysis | l'analyse isotopique
--
marine core | les carottes marines
fossil coral | les coraux fossiles
mollusc shell | les coquilles de mollusques
foraminifera | les foraminifères
tephra layer | les niveaux de téphra
coastal peat | les tourbes littorales
beach ridge | les anciens cordons de plage
lagoon sediment | les sédiments lagunaires
fossil reef | les récifs fossiles
sediment drift | les dérives sédimentaires
coral microatoll | les micro-atolls coralliens
salt marsh core | les carottes de marais salés
coastal dune sequence | les séquences dunaires littorales
deep-sea core | les carottes profondes
estuarine core | les carottes estuariennes
midden | les amas coquilliers
raised beach | les plages soulevées
submerged forest | les forêts submergées
sabkha | les sebkhas
coastal lake | les lacs littoraux
shelf core | les carottes du plateau
fossil oyster bank | les bancs d'huîtres fossiles
fossil beach | les plages fossiles
foraminiferal ooze | les boues à foraminifères
==
variability | la variabilité
reconstruction | la reconstruction
~ proxy records | les enregistrements indirects
trends | les tendances
~ modelling | la modélisation
--
past storminess | la tempétuosité passée
bottom water temperature | la température des eaux de fond
sea ice extent | l'extension de la glace de mer
upwelling intensity | l'intensité de l'upwelling
deep-water circulation | la circulation profonde
dust deposition | les dépôts de poussières
monsoon intensity | l'intensité de la mousson
ocean oxygenation | l'oxygénation océanique
sediment flux | les flux sédimentaires
surface salinity | la salinité de surface
ice sheet extent | l'extension des calottes
glacial runoff | les apports glaciaires
El Niño activity | l'activité d'El Niño
==
transfer functions | les fonctions de transfert
preservation | la préservation
abundance changes | les variations d'abondance
stable isotopes | les isotopes stables
~ counting | le comptage
--
ostracod | les ostracodes
diatom frustule | les frustules de diatomées
testate amoeba | les thécamoebiens
pollen grain | les grains de pollen
chironomid | les chironomes
radiolarian test | les tests de radiolaires
"""


def _base_forms() -> tuple[set[str], set[str]]:
    """English and French forms of every base term, to keep compounds distinct."""
    en, fr = set(), set()
    for block in (
        _GEOMORPHOLOGY, _CIRCULATION, _ECOLOGY, _FISHERIES, _PLANKTON, _HAZARDS,
        _OBSERVATION, _POLLUTION, _GOVERNANCE, _BLUE_ECONOMY, _ESTUARIES, _PALEO,
    ):  # fmt: skip
        for term in parse_terms(block):
            en.add(term.en.lower())
            fr.add(term.fr_def)
    return en, fr


_BASE_EN, _BASE_FR = _base_forms()


def compose_terms(block: str) -> tuple[Term, ...]:
    """Compound terms of a families block (see the comment above the blocks).

    A compound that repeats a base term of any theme, in either language, is
    left out, so every English form keeps a single French form.
    """
    out: list[Term] = []
    seen: set[str] = set()
    for family in block.strip().split("=="):
        aspects_block, _, objects_block = family.partition("--")
        aspects = parse_terms(aspects_block)
        objects = parse_terms(objects_block)
        if not aspects or not objects:
            raise ValueError("a family needs aspects, '--', then objects")
        for obj in objects:
            for aspect in aspects:
                # Not every aspect applies to every object: keep a fixed,
                # evenly spread share of the pairs (crc32 is stable across
                # platforms and Python versions, unlike hash()).
                if zlib.crc32(f"{obj.en}|{aspect.en}".encode()) % 100 >= COMPOUND_DENSITY:
                    continue
                term = Term(
                    en=f"{obj.en} {aspect.en}",
                    fr=f"{aspect.fr} {obj.fr_de}",
                    article=aspect.article,
                    technique=aspect.technique,
                    focus=obj.en,
                )
                key = term.en.lower()
                if key in _BASE_EN or term.fr_def in _BASE_FR or key in seen:
                    continue
                seen.add(key)
                out.append(term)
    return tuple(out)


THEMES: tuple[Theme, ...] = (
    Theme(
        id="coastal-geomorphology",
        name_en="Coastal geomorphology and sediment transport",
        name_fr="Géomorphologie côtière et transport sédimentaire",
        kind="natural",
        neighbours=(
            "coastal-hazards",
            "estuaries-wetlands",
            "ocean-observation",
            "ocean-circulation",
        ),
        group_stems=("Coastal Dynamics", "Littoral Morphodynamics", "Shoreline Systems"),
        terms=parse_terms(_GEOMORPHOLOGY) + compose_terms(_GEOMORPHOLOGY_FAMILIES),
    ),
    Theme(
        id="ocean-circulation",
        name_en="Ocean circulation and tides",
        name_fr="Circulation océanique et marées",
        kind="natural",
        neighbours=(
            "ocean-observation",
            "plankton-biogeochemistry",
            "coastal-geomorphology",
            "paleoceanography",
        ),
        group_stems=("Physical Oceanography", "Tides and Currents", "Shelf Dynamics"),
        terms=parse_terms(_CIRCULATION) + compose_terms(_CIRCULATION_FAMILIES),
    ),
    Theme(
        id="marine-ecology",
        name_en="Marine ecology and biodiversity",
        name_fr="Écologie marine et biodiversité",
        kind="natural",
        neighbours=(
            "fisheries-aquaculture",
            "plankton-biogeochemistry",
            "estuaries-wetlands",
            "marine-pollution",
        ),
        group_stems=("Marine Ecology", "Benthic Ecology", "Marine Biodiversity"),
        terms=parse_terms(_ECOLOGY) + compose_terms(_ECOLOGY_FAMILIES),
    ),
    Theme(
        id="fisheries-aquaculture",
        name_en="Fisheries and aquaculture",
        name_fr="Pêche et aquaculture",
        kind="natural",
        neighbours=("marine-ecology", "coastal-governance", "blue-economy"),
        group_stems=("Fisheries Science", "Aquaculture Research", "Living Resources"),
        terms=parse_terms(_FISHERIES) + compose_terms(_FISHERIES_FAMILIES),
    ),
    Theme(
        id="plankton-biogeochemistry",
        name_en="Plankton and biogeochemistry",
        name_fr="Plancton et biogéochimie",
        kind="natural",
        neighbours=(
            "marine-ecology",
            "ocean-circulation",
            "marine-pollution",
            "paleoceanography",
        ),
        group_stems=("Marine Biogeochemistry", "Plankton Ecology", "Ocean Carbon"),
        terms=parse_terms(_PLANKTON) + compose_terms(_PLANKTON_FAMILIES),
    ),
    Theme(
        id="coastal-hazards",
        name_en="Coastal hazards and sea-level rise",
        name_fr="Risques côtiers et élévation du niveau de la mer",
        kind="natural",
        neighbours=(
            "coastal-geomorphology",
            "coastal-governance",
            "ocean-circulation",
            "estuaries-wetlands",
        ),
        group_stems=("Coastal Risks", "Sea-Level and Hazards", "Coastal Flooding"),
        terms=parse_terms(_HAZARDS) + compose_terms(_HAZARDS_FAMILIES),
    ),
    Theme(
        id="ocean-observation",
        name_en="Ocean observation and remote sensing",
        name_fr="Observation de l'océan et télédétection",
        kind="natural",
        neighbours=(
            "ocean-circulation",
            "coastal-geomorphology",
            "plankton-biogeochemistry",
            "coastal-hazards",
        ),
        group_stems=("Ocean Observation", "Marine Remote Sensing", "Ocean Instrumentation"),
        terms=parse_terms(_OBSERVATION) + compose_terms(_OBSERVATION_FAMILIES),
    ),
    Theme(
        id="marine-pollution",
        name_en="Marine pollution and microplastics",
        name_fr="Pollution marine et microplastiques",
        kind="natural",
        neighbours=(
            "plankton-biogeochemistry",
            "marine-ecology",
            "blue-economy",
            "fisheries-aquaculture",
        ),
        group_stems=("Marine Contaminants", "Coastal Ecotoxicology", "Plastics and Pollutants"),
        terms=parse_terms(_POLLUTION) + compose_terms(_POLLUTION_FAMILIES),
    ),
    Theme(
        id="coastal-governance",
        name_en="Coastal communities and governance",
        name_fr="Sociétés littorales et gouvernance",
        kind="social",
        neighbours=("coastal-hazards", "fisheries-aquaculture", "blue-economy"),
        group_stems=("Coastal Societies", "Maritime Governance", "Littoral Territories"),
        terms=parse_terms(_GOVERNANCE) + compose_terms(_GOVERNANCE_FAMILIES),
    ),
    Theme(
        id="blue-economy",
        name_en="Ports, shipping and the blue economy",
        name_fr="Ports, transport maritime et économie bleue",
        kind="social",
        neighbours=("coastal-governance", "marine-pollution", "fisheries-aquaculture"),
        group_stems=("Maritime Economics", "Ports and Shipping", "Blue Economy"),
        terms=parse_terms(_BLUE_ECONOMY) + compose_terms(_BLUE_ECONOMY_FAMILIES),
    ),
    Theme(
        id="estuaries-wetlands",
        name_en="Estuaries and wetlands",
        name_fr="Estuaires et zones humides",
        kind="natural",
        neighbours=(
            "coastal-geomorphology",
            "marine-ecology",
            "coastal-hazards",
            "plankton-biogeochemistry",
        ),
        group_stems=("Estuarine Systems", "Wetland Ecology", "Lagoons and Marshes"),
        terms=parse_terms(_ESTUARIES) + compose_terms(_ESTUARIES_FAMILIES),
    ),
    Theme(
        id="paleoceanography",
        name_en="Paleoceanography and climate archives",
        name_fr="Paléocéanographie et archives climatiques",
        kind="natural",
        neighbours=("ocean-circulation", "plankton-biogeochemistry", "coastal-geomorphology"),
        group_stems=("Paleoclimate", "Marine Geology", "Sedimentary Archives"),
        terms=parse_terms(_PALEO) + compose_terms(_PALEO_FAMILIES),
    ),
)

THEME_BY_ID: dict[str, Theme] = {t.id: t for t in THEMES}

# ---------------------------------------------------------------------------
# Methods, settings and drivers shared by every theme
# ---------------------------------------------------------------------------

_METHODS_NATURAL = """
numerical modelling | la modélisation numérique
field surveys | les campagnes de terrain
in situ measurements | les mesures in situ
flume experiments | les expériences en canal hydraulique
time series analysis | l'analyse de séries temporelles
generalised additive models | les modèles additifs généralisés
mixed-effects models | les modèles à effets mixtes
principal component analysis | l'analyse en composantes principales
spatial statistics | la statistique spatiale
stable isotope analysis | l'analyse des isotopes stables
mass balance approaches | les approches par bilan de masse
ensemble simulations | les simulations d'ensemble
long-term monitoring | le suivi à long terme
mesocosm experiments | les expériences en mésocosmes
high-throughput sequencing | le séquençage à haut débit
geochemical analyses | les analyses géochimiques
acoustic surveys | les relevés acoustiques
spectral analysis | l'analyse spectrale
wavelet analysis | l'analyse en ondelettes
high-resolution simulations | les simulations à haute résolution
coupled ocean-wave models | les modèles couplés océan-vagues
trend analysis | l'analyse de tendances
hindcast simulations | les simulations rétrospectives
field sampling | l'échantillonnage de terrain
deep learning | l'apprentissage profond
sensitivity analysis | l'analyse de sensibilité
uncertainty quantification | la quantification des incertitudes
remote sensing | la télédétection
"""

_METHODS_SOCIAL = """
comparative case analysis | l'analyse comparative de cas
documentary analysis | l'analyse documentaire
archival research | le travail d'archives
participant observation | l'observation participante
mixed methods | les méthodes mixtes
econometric models | les modèles économétriques
participatory mapping | la cartographie participative
semi-structured interviews | les entretiens semi-directifs
focus groups | les groupes de discussion
ethnographic fieldwork | le terrain ethnographique
institutional analysis | l'analyse institutionnelle
public policy analysis | l'analyse des politiques publiques
discourse analysis | l'analyse de discours
questionnaire surveys | les enquêtes par questionnaire
household surveys | les enquêtes auprès des ménages
social network analysis | l'analyse des réseaux sociaux
qualitative content analysis | l'analyse de contenu qualitative
"""

_METHODS_ANY = """
machine learning | l'apprentissage automatique
Bayesian inference | l'inférence bayésienne
scenario analysis | l'analyse de scénarios
meta-analysis | la méta-analyse
geographic information systems | les systèmes d'information géographique
network analysis | l'analyse de réseaux
agent-based modelling | la modélisation multi-agents
cluster analysis | la classification hiérarchique
"""

METHODS: tuple[Method, ...] = (
    tuple(Method(t, "natural") for t in parse_terms(_METHODS_NATURAL))
    + tuple(Method(t, "social") for t in parse_terms(_METHODS_SOCIAL))
    + tuple(Method(t, "any") for t in parse_terms(_METHODS_ANY))
)

# Locative phrases: where a study takes place.
SETTINGS: tuple[Setting, ...] = (
    Setting("on sandy beaches", "sur les plages sableuses", social=True),
    Setting("on rocky coasts", "sur les côtes rocheuses", social=True),
    Setting("in semi-enclosed bays", "dans les baies semi-fermées", social=True),
    Setting("on the continental shelf", "sur le plateau continental"),
    Setting("in shallow coastal waters", "dans les eaux côtières peu profondes"),
    Setting("in temperate coastal seas", "dans les mers côtières tempérées"),
    Setting("on tropical coastlines", "sur les littoraux tropicaux", social=True),
    Setting("in high-latitude fjords", "dans les fjords de haute latitude"),
    Setting("on urbanised coastlines", "sur les littoraux urbanisés", social=True),
    Setting("on tide-dominated coasts", "sur les côtes dominées par la marée"),
    Setting("on wave-dominated coasts", "sur les côtes dominées par la houle"),
    Setting("in microtidal environments", "dans les environnements microtidaux"),
    Setting("in deltaic systems", "dans les systèmes deltaïques", social=True),
    Setting("on barrier coasts", "sur les côtes à cordons littoraux", social=True),
    Setting("in the open ocean", "au large"),
    Setting("in enclosed seas", "dans les mers fermées"),
    Setting("in eastern boundary upwelling regions", "dans les régions d'upwelling de bord est"),
    Setting("on small islands", "dans les petites îles", social=True),
    Setting("in the intertidal zone", "dans la zone intertidale"),
    Setting("in the coastal zone", "dans la zone côtière", social=True),
)

DRIVERS: tuple[Term, ...] = parse_terms(
    """
climate change | le changement climatique
anthropogenic pressures | les pressions anthropiques
extreme events | les événements extrêmes
interannual variability | la variabilité interannuelle
seasonal forcing | le forçage saisonnier
long-term warming | le réchauffement à long terme
land-use change | les changements d'usage des sols
coastal development | l'aménagement du littoral
storm events | les épisodes de tempête
river inputs | les apports fluviaux
"""
)


def all_terms() -> tuple[Term, ...]:
    """Every distinct theme term (bridge terms once), in theme order."""
    seen: dict[str, Term] = {}
    for theme in THEMES:
        for term in theme.terms:
            seen.setdefault(term.en, term)
    return tuple(seen.values())
