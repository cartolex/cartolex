# SPDX-License-Identifier: MIT
"""Vocabulary of the invented coastal and marine research community.

Twelve themes, each a list of technical terms written as
``English | French | Portuguese`` lines. The French and Portuguese forms carry
their definite article (``le``, ``la``, ``l'``, ``les``; ``o``, ``a``, ``os``,
``as``) so that the text templates can build grammatical sentences: ``du``,
``de la``, ``au``, ``aux`` in French; ``do``, ``da``, ``no``, ``pela``, ``ao``,
``às`` in Portuguese (Brazilian spelling). A term listed in two themes (same
forms) is a *bridge* term: themes overlap, as they do in a real field.

Everything here is data: importing the module parses the lists and checks
their shape, nothing else.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

ARTICLES = ("le", "la", "l'", "les")
PT_ARTICLES = ("o", "a", "os", "as")
# Percentage of object-aspect pairs of a family kept as compound terms.
COMPOUND_DENSITY = 100


@dataclass(frozen=True)
class Term:
    """A technical term in English, French and Portuguese.

    ``fr`` is the bare French form (no article); ``article`` is its definite
    article, one of :data:`ARTICLES`. ``pt`` and ``pt_article`` are the same
    for Portuguese (:data:`PT_ARTICLES`); they are empty for a term written
    in two languages only.
    """

    en: str
    fr: str
    article: str
    technique: bool = False  # a tool or approach rather than a phenomenon or topic
    focus: str = ""  # for a compound term, the object it is about ("hake")
    pt: str = ""
    pt_article: str = ""

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

    def _pt_with(self, forms: dict[str, str]) -> str:
        if not self.pt:
            raise ValueError(f"no Portuguese form for {self.en!r}")
        return f"{forms[self.pt_article]} {self.pt}"

    @property
    def pt_def(self) -> str:
        """Portuguese form with its definite article (``o transporte de sedimentos``)."""
        return self._pt_with({a: a for a in PT_ARTICLES})

    @property
    def pt_de(self) -> str:
        """Portuguese form after ``de`` (``do``, ``da``, ``dos``, ``das``)."""
        return self._pt_with({"o": "do", "a": "da", "os": "dos", "as": "das"})

    @property
    def pt_em(self) -> str:
        """Portuguese form after ``em`` (``no``, ``na``, ``nos``, ``nas``)."""
        return self._pt_with({"o": "no", "a": "na", "os": "nos", "as": "nas"})

    @property
    def pt_a(self) -> str:
        """Portuguese form after ``a`` (``ao``, ``à``, ``aos``, ``às``)."""
        return self._pt_with({"o": "ao", "a": "à", "os": "aos", "as": "às"})

    @property
    def pt_por(self) -> str:
        """Portuguese form after ``por`` (``pelo``, ``pela``, ``pelos``, ``pelas``)."""
        return self._pt_with({"o": "pelo", "a": "pela", "os": "pelos", "as": "pelas"})


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
    name_pt: str = ""

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
    """A place where a study happens, as a locative phrase in each language.

    ``social`` marks settings that also suit social-science works (a coastline
    people live on, not the open ocean).
    """

    en: str
    fr: str
    social: bool = False
    pt: str = ""


def _split_article(
    form: str, articles: tuple[str, ...], line: str, language: str
) -> tuple[str, str]:
    if "l'" in articles and form.startswith("l'"):
        return "l'", form[2:]
    article, _, rest = form.partition(" ")
    if article not in articles or not rest:
        raise ValueError(f"{language} form needs a definite article: {line!r}")
    return article, rest


def parse_terms(block: str) -> tuple[Term, ...]:
    """Parse ``English | French-with-article [| Portuguese-with-article]`` lines into terms.

    A leading ``~`` marks a technique (a tool or approach: the templates use
    it where a method goes). Blank lines and lines starting with ``#`` are
    skipped. Raises :class:`ValueError` on a line without two or three
    ``|``-separated forms, or on a French or Portuguese form without a
    definite article.
    """
    terms: list[Term] = []
    for raw in block.strip().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        technique = line.startswith("~")
        line = line.lstrip("~ ")
        parts = [p.strip() for p in line.split("|")]
        if len(parts) not in (2, 3) or not all(parts):
            raise ValueError(f"bad term line: {line!r}")
        en, fr = parts[0], parts[1]
        article, fr_bare = _split_article(fr, ARTICLES, line, "French")
        pt_article, pt = "", ""
        if len(parts) == 3:
            pt_article, pt = _split_article(parts[2], PT_ARTICLES, line, "Portuguese")
        terms.append(Term(en, fr_bare, article, technique, pt=pt, pt_article=pt_article))
    return tuple(terms)


# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------

_GEOMORPHOLOGY = """
sediment transport | le transport sédimentaire | o transporte de sedimentos
longshore drift | la dérive littorale | a deriva litorânea
cross-shore sediment exchange | les échanges sédimentaires transversaux | as trocas sedimentares transversais
beach morphodynamics | la morphodynamique des plages | a morfodinâmica praial
sandbar migration | la migration des barres sableuses | a migração de bancos arenosos
dune erosion | l'érosion dunaire | a erosão de dunas
foredune recovery | la régénération des avant-dunes | a recuperação das dunas frontais
aeolian sand transport | le transport éolien de sable | o transporte eólico de areia
shoreline change | l'évolution du trait de côte | a variação da linha de costa
~ beach profile surveys | les levés de profils de plage | os levantamentos de perfis praiais
grain size distribution | la distribution granulométrique | a distribuição granulométrica
bedload transport | le transport par charriage | o transporte por arrasto de fundo
suspended sediment concentration | la concentration en matières en suspension | a concentração de sedimentos em suspensão
ripple formation | la formation des rides sédimentaires | a formação de marcas onduladas
tidal inlet dynamics | la dynamique des embouchures tidales | a dinâmica de desembocaduras de maré
ebb-tidal delta | le delta de jusant | o delta de maré vazante
spit elongation | l'allongement des flèches littorales | o alongamento de pontais arenosos
barrier island migration | la migration des îles-barrières | a migração de ilhas-barreira
shore platform erosion | l'érosion des plates-formes littorales | a erosão de plataformas de abrasão
cliff retreat | le recul des falaises | o recuo de falésias
rock coast erosion | l'érosion des côtes rocheuses | a erosão de costas rochosas
gravel beach dynamics | la dynamique des plages de galets | a dinâmica de praias de cascalho
sediment budget | le bilan sédimentaire | o balanço sedimentar
littoral sediment cells | les cellules hydrosédimentaires | as células sedimentares litorâneas
nearshore bathymetry | la bathymétrie de l'avant-côte | a batimetria da antepraia
wave-driven currents | les courants induits par les vagues | as correntes induzidas por ondas
rip currents | les courants d'arrachement | as correntes de retorno
swash zone processes | les processus de la zone de jet de rive | os processos da zona de espraiamento
surf zone hydrodynamics | l'hydrodynamique de la zone de déferlement | a hidrodinâmica da zona de arrebentação
sediment sorting | le tri granulométrique | a seleção granulométrica
mixed sand-gravel beaches | les plages mixtes de sable et de galets | as praias mistas de areia e cascalho
beach nourishment | le rechargement en sable des plages | a alimentação artificial de praias
~ morphodynamic modelling | la modélisation morphodynamique | a modelagem morfodinâmica
equilibrium beach profiles | les profils de plage d'équilibre | os perfis praiais de equilíbrio
storm-induced erosion | l'érosion induite par les tempêtes | a erosão induzida por tempestades
overwash deposits | les dépôts de débordement | os depósitos de sobrelavagem
sediment provenance | la provenance des sédiments | a proveniência sedimentar
shoreface dynamics | la dynamique de l'avant-côte | a dinâmica da face litorânea
subtidal sandbanks | les bancs sableux subtidaux | os bancos arenosos submareais
sand wave migration | la migration des dunes sous-marines | a migração de ondas de areia
tidal flat morphology | la morphologie des estrans vaseux | a morfologia de planícies de maré
cohesive sediment dynamics | la dynamique des sédiments cohésifs | a dinâmica de sedimentos coesivos
flocculation processes | les processus de floculation | os processos de floculação
erosion thresholds | les seuils d'érosion | os limiares de erosão
critical shear stress | la contrainte de cisaillement critique | a tensão de cisalhamento crítica
bed shear stress | la contrainte de cisaillement sur le fond | a tensão de cisalhamento no fundo
sediment resuspension | la remise en suspension des sédiments | a ressuspensão de sedimentos
coastal morphodynamics | la morphodynamique côtière | a morfodinâmica costeira
~ shoreline mapping | la cartographie du trait de côte | o mapeamento da linha de costa
beach rotation | la rotation des plages | a rotação praial
headland bypassing | le contournement des caps | a transposição de promontórios
embayed beaches | les plages de poche | as praias de enseada
~ sediment tracer experiments | les expériences de traçage sédimentaire | os experimentos com traçadores sedimentares
~ heavy mineral analysis | l'analyse des minéraux lourds | a análise de minerais pesados
fluvial sediment supply | les apports sédimentaires fluviaux | o aporte sedimentar fluvial
delta progradation | la progradation des deltas | a progradação deltaica
coastal dune vegetation | la végétation des dunes littorales | a vegetação de dunas costeiras
dune blowout dynamics | la dynamique des caoudeyres | a dinâmica de bacias de deflação
~ video beach monitoring | le suivi vidéo des plages | o monitoramento de praias por vídeo
beach cusps | les croissants de plage | as cúspides praiais
berm formation | la formation des bermes | a formação de bermas
longshore sediment flux | le flux sédimentaire longitudinal | o fluxo sedimentar longitudinal
morphological equilibrium | l'équilibre morphologique | o equilíbrio morfológico
sediment connectivity | la connectivité sédimentaire | a conectividade sedimentar
shingle ridges | les cordons de galets | os cordões de cascalho
dune stabilisation | la stabilisation des dunes | a estabilização de dunas
coastal geomorphology | la géomorphologie côtière | a geomorfologia costeira
wave runup | la hauteur de jet de rive | o espraiamento máximo das ondas
infragravity waves | les ondes infragravitaires | as ondas de infragravidade
sandbar welding | le rattachement des barres au haut de plage | a soldagem de bancos à face praial
~ shoreline response modelling | la modélisation de la réponse du trait de côte | a modelagem da resposta da linha de costa
~ grain shape analysis | la morphoscopie des grains | a morfoscopia de grãos
beach groundwater | la nappe de plage | o lençol freático praial
~ cliff monitoring | le suivi des falaises côtières | o monitoramento de falésias costeiras
marine aggregate extraction | l'extraction de granulats marins | a extração de agregados marinhos
"""

_CIRCULATION = """
~ tidal harmonic analysis | l'analyse harmonique de la marée | a análise harmônica da maré
internal tides | les marées internes | as marés internas
tidal asymmetry | l'asymétrie de la marée | a assimetria da maré
tidal currents | les courants de marée | as correntes de maré
residual circulation | la circulation résiduelle | a circulação residual
shelf circulation | la circulation sur le plateau continental | a circulação na plataforma continental
coastal upwelling | l'upwelling côtier | a ressurgência costeira
Ekman transport | le transport d'Ekman | o transporte de Ekman
mesoscale eddies | les tourbillons de méso-échelle | os vórtices de mesoescala
submesoscale fronts | les fronts de sous-méso-échelle | as frentes de submesoescala
geostrophic currents | les courants géostrophiques | as correntes geostróficas
western boundary currents | les courants de bord ouest | as correntes de contorno oeste
thermohaline circulation | la circulation thermohaline | a circulação termohalina
water mass formation | la formation des masses d'eau | a formação de massas d'água
vertical mixing | le mélange vertical | a mistura vertical
turbulent kinetic energy | l'énergie cinétique turbulente | a energia cinética turbulenta
stratification dynamics | la dynamique de la stratification | a dinâmica da estratificação
mixed layer depth | la profondeur de la couche de mélange | a profundidade da camada de mistura
river plume dynamics | la dynamique des panaches fluviaux | a dinâmica de plumas fluviais
estuarine circulation | la circulation estuarienne | a circulação estuarina
wind-driven circulation | la circulation induite par le vent | a circulação forçada pelo vento
storm surge propagation | la propagation des surcotes de tempête | a propagação de marés meteorológicas
tidal energy dissipation | la dissipation de l'énergie de marée | a dissipação da energia de maré
shelf sea dynamics | la dynamique des mers épicontinentales | a dinâmica de mares epicontinentais
coastal trapped waves | les ondes piégées à la côte | as ondas aprisionadas à costa
Kelvin wave propagation | la propagation des ondes de Kelvin | a propagação de ondas de Kelvin
inertial oscillations | les oscillations inertielles | as oscilações inerciais
baroclinic instability | l'instabilité barocline | a instabilidade baroclínica
frontal systems | les systèmes frontaux | os sistemas frontais
tidal mixing fronts | les fronts de mélange tidal | as frentes de mistura de maré
ocean heat content | le contenu thermique de l'océan | o conteúdo de calor do oceano
sea surface temperature | la température de surface de la mer | a temperatura da superfície do mar
salinity gradients | les gradients de salinité | os gradientes de salinidade
density-driven flows | les écoulements induits par la densité | os escoamentos induzidos pela densidade
~ hydrodynamic modelling | la modélisation hydrodynamique | a modelagem hidrodinâmica
~ regional ocean models | les modèles océaniques régionaux | os modelos oceânicos regionais
~ unstructured grid models | les modèles à maillage non structuré | os modelos de grade não estruturada
~ open boundary conditions | les conditions aux limites ouvertes | as condições de contorno abertas
~ current profiling | le profilage des courants | a perfilagem de correntes
~ drifter trajectories | les trajectoires de bouées dérivantes | as trajetórias de derivadores
~ Lagrangian particle tracking | le suivi lagrangien de particules | o rastreamento lagrangiano de partículas
water residence time | le temps de résidence des eaux | o tempo de residência da água
larval connectivity | la connectivité larvaire | a conectividade larval
slope currents | les courants de pente | as correntes de talude
canyon upwelling | l'upwelling dans les canyons sous-marins | a ressurgência em cânions submarinos
internal wave breaking | le déferlement des ondes internes | a quebra de ondas internas
harbour seiches | les seiches portuaires | as seichas portuárias
sea level variability | la variabilité du niveau marin | a variabilidade do nível do mar
~ tide gauge records | les enregistrements marégraphiques | os registros maregráficos
~ tidal prediction | la prédiction de la marée | a previsão de marés
wave-current interactions | les interactions houle-courant | as interações onda-corrente
Stokes drift | la dérive de Stokes | a deriva de Stokes
Langmuir circulation | la circulation de Langmuir | a circulação de Langmuir
air-sea fluxes | les flux air-mer | os fluxos oceano-atmosfera
wind stress forcing | le forçage par la tension du vent | o forçamento pela tensão do vento
~ ocean reanalysis products | les produits de réanalyse océanique | os produtos de reanálise oceânica
meridional overturning circulation | la circulation méridienne de retournement | a circulação meridional de revolvimento
potential vorticity | la vorticité potentielle | a vorticidade potencial
topographic steering | le guidage topographique des courants | o direcionamento topográfico das correntes
tidal rectification | la rectification tidale | a retificação de maré
freshwater fluxes | les apports d'eau douce | os aportes de água doce
coastal jets | les jets côtiers | os jatos costeiros
eddy kinetic energy | l'énergie cinétique tourbillonnaire | a energia cinética turbulenta de vórtices
~ vertical velocity estimates | les estimations de vitesse verticale | as estimativas de velocidade vertical
tidal resonance | la résonance tidale | a ressonância de maré
nonlinear tidal constituents | les composantes non linéaires de la marée | as componentes não lineares da maré
~ ocean circulation modelling | la modélisation de la circulation océanique | a modelagem da circulação oceânica
~ mooring arrays | les réseaux de mouillages | os arranjos de fundeios
~ hydrographic sections | les sections hydrographiques | as seções hidrográficas
deep water formation | la formation des eaux profondes | a formação de águas profundas
"""

_ECOLOGY = """
benthic communities | les communautés benthiques | as comunidades bentônicas
seagrass meadows | les herbiers de phanérogames marines | os prados de fanerógamas marinhas
kelp forests | les forêts de laminaires | as florestas de kelp
coral reef resilience | la résilience des récifs coralliens | a resiliência dos recifes de coral
~ species distribution models | les modèles de distribution d'espèces | os modelos de distribuição de espécies
functional diversity | la diversité fonctionnelle | a diversidade funcional
trophic interactions | les interactions trophiques | as interações tróficas
food web structure | la structure des réseaux trophiques | a estrutura das teias tróficas
marine protected areas | les aires marines protégées | as áreas marinhas protegidas
~ habitat mapping | la cartographie des habitats | o mapeamento de habitats
non-indigenous species | les espèces non indigènes | as espécies não nativas
biological invasions | les invasions biologiques | as invasões biológicas
population connectivity | la connectivité des populations | a conectividade entre populações
larval dispersal | la dispersion larvaire | a dispersão larval
recruitment variability | la variabilité du recrutement | a variabilidade do recrutamento
intertidal zonation | la zonation intertidale | a zonação entremarés
rocky intertidal assemblages | les peuplements intertidaux rocheux | as assembleias de costões rochosos
soft-sediment macrofauna | la macrofaune des sédiments meubles | a macrofauna de fundos inconsolidados
bioturbation processes | les processus de bioturbation | os processos de bioturbação
ecosystem engineers | les espèces ingénieures | as espécies engenheiras
biogenic reefs | les récifs biogènes | os recifes biogênicos
oyster reefs | les récifs d'huîtres | os recifes de ostras
~ environmental DNA | l'ADN environnemental | o DNA ambiental
~ metabarcoding surveys | les inventaires par métabarcodage | os inventários por metabarcoding
species richness | la richesse spécifique | a riqueza de espécies
beta diversity | la diversité bêta | a diversidade beta
community assembly | l'assemblage des communautés | a montagem de comunidades
ocean warming impacts | les impacts du réchauffement océanique | os impactos do aquecimento oceânico
marine heatwaves | les vagues de chaleur marines | as ondas de calor marinhas
thermal tolerance | la tolérance thermique | a tolerância térmica
calcifying organisms | les organismes calcifiants | os organismos calcificadores
seabird foraging | la recherche alimentaire des oiseaux marins | o forrageamento de aves marinhas
marine mammal distribution | la distribution des mammifères marins | a distribuição de mamíferos marinhos
~ cetacean acoustic monitoring | le suivi acoustique des cétacés | o monitoramento acústico de cetáceos
fish assemblages | les peuplements de poissons | as assembleias de peixes
nursery habitats | les habitats de nourricerie | os habitats de berçário
deep-sea ecosystems | les écosystèmes profonds | os ecossistemas de mar profundo
hydrothermal vent fauna | la faune des sources hydrothermales | a fauna de fontes hidrotermais
biodiversity hotspots | les points chauds de biodiversité | os hotspots de biodiversidade
ecosystem functioning | le fonctionnement des écosystèmes | o funcionamento dos ecossistemas
predator-prey dynamics | la dynamique prédateur-proie | a dinâmica predador-presa
keystone species | les espèces clés de voûte | as espécies-chave
trophic cascades | les cascades trophiques | as cascatas tróficas
~ stable isotope ecology | l'écologie des isotopes stables | a ecologia de isótopos estáveis
habitat fragmentation | la fragmentation des habitats | a fragmentação de habitats
species range shifts | les déplacements d'aires de répartition | os deslocamentos de áreas de distribuição
phenological shifts | les décalages phénologiques | as mudanças fenológicas
macroalgal communities | les communautés de macroalgues | as comunidades de macroalgas
epibenthic megafauna | la mégafaune épibenthique | a megafauna epibentônica
sponge grounds | les champs d'éponges | os bancos de esponjas
cold-water corals | les coraux d'eau froide | os corais de águas frias
reef fish behaviour | le comportement des poissons récifaux | o comportamento de peixes recifais
~ biodiversity indicators | les indicateurs de biodiversité | os indicadores de biodiversidade
~ ecological niche modelling | la modélisation de niche écologique | a modelagem de nicho ecológico
benthic-pelagic coupling | le couplage benthopélagique | o acoplamento bento-pelágico
seascape connectivity | la connectivité du paysage marin | a conectividade da paisagem marinha
ecological restoration | la restauration écologique | a restauração ecológica
seagrass restoration | la restauration des herbiers | a restauração de pradarias marinhas
mussel beds | les moulières | os bancos de mexilhões
population genetics | la génétique des populations | a genética de populações
genetic connectivity | la connectivité génétique | a conectividade genética
~ marine biodiversity monitoring | le suivi de la biodiversité marine | o monitoramento da biodiversidade marinha
~ underwater visual census | les comptages visuels en plongée | os censos visuais subaquáticos
~ baited remote underwater video | la vidéo sous-marine appâtée | o vídeo subaquático remoto com isca
functional traits | les traits fonctionnels | os traços funcionais
size spectra | les spectres de taille | os espectros de tamanho
coral bleaching | le blanchissement des coraux | o branqueamento de corais
artificial reefs | les récifs artificiels | os recifes artificiais
ocean acidification effects | les effets de l'acidification des océans | os efeitos da acidificação oceânica
saltmarsh fauna | la faune des prés salés | a fauna de marismas
"""

_FISHERIES = """
~ stock assessment | l'évaluation des stocks | a avaliação de estoques
fishing mortality | la mortalité par pêche | a mortalidade por pesca
spawning stock biomass | la biomasse du stock reproducteur | a biomassa do estoque reprodutor
catch per unit effort | les captures par unité d'effort | a captura por unidade de esforço
maximum sustainable yield | le rendement maximal durable | o rendimento máximo sustentável
bycatch reduction | la réduction des captures accessoires | a redução da captura incidental
small-scale fisheries | la pêche artisanale | a pesca artesanal
fishing effort allocation | la répartition de l'effort de pêche | a alocação do esforço de pesca
fleet dynamics | la dynamique des flottilles | a dinâmica de frotas
~ vessel monitoring data | les données de suivi des navires | os dados de rastreamento de embarcações
fishery discards | les rejets de pêche | os descartes da pesca
gear selectivity | la sélectivité des engins de pêche | a seletividade de petrechos de pesca
bottom trawling impacts | les impacts du chalutage de fond | os impactos do arrasto de fundo
~ age-structured models | les modèles structurés en âge | os modelos estruturados por idade
~ length-based indicators | les indicateurs fondés sur la taille | os indicadores baseados em comprimento
~ recruitment forecasting | la prévision du recrutement | a previsão do recrutamento
~ otolith microchemistry | la microchimie des otolithes | a microquímica de otólitos
~ growth rate estimation | l'estimation des taux de croissance | a estimativa de taxas de crescimento
fisheries management | la gestion des pêches | a gestão pesqueira
ecosystem-based fisheries management | la gestion écosystémique des pêches | a gestão pesqueira ecossistêmica
harvest control rules | les règles de contrôle des captures | as regras de controle de captura
total allowable catch | le total admissible des captures | a captura total permitida
fishing quotas | les quotas de pêche | as cotas de pesca
shellfish aquaculture | la conchyliculture | a malacocultura
oyster farming | l'ostréiculture | a ostreicultura
mussel farming | la mytiliculture | a mitilicultura
finfish aquaculture | la pisciculture marine | a piscicultura marinha
integrated multi-trophic aquaculture | l'aquaculture multitrophique intégrée | a aquicultura multitrófica integrada
offshore aquaculture | l'aquaculture au large | a aquicultura em mar aberto
aquaculture carrying capacity | la capacité de charge des bassins conchylicoles | a capacidade de suporte de áreas aquícolas
seaweed cultivation | l'algoculture | a algicultura
~ shellfish growth models | les modèles de croissance des coquillages | os modelos de crescimento de moluscos
shellfish mortality outbreaks | les épisodes de mortalité conchylicole | os eventos de mortalidade de moluscos
oyster herpesvirus | l'herpèsvirus de l'huître | o herpesvírus de ostras
hatchery production | la production en écloserie | a produção em laboratório de larvicultura
feed conversion efficiency | l'efficacité de conversion alimentaire | a eficiência de conversão alimentar
fish welfare | le bien-être des poissons | o bem-estar de peixes
recirculating aquaculture systems | les systèmes aquacoles en recirculation | os sistemas de aquicultura com recirculação
stock-recruitment relationships | les relations stock-recrutement | as relações estoque-recrutamento
fisheries-induced evolution | l'évolution induite par la pêche | a evolução induzida pela pesca
size at maturity | la taille à maturité | o tamanho de maturação
~ fish condition indices | les indices de condition des poissons | os índices de condição de peixes
~ acoustic fish surveys | les campagnes acoustiques halieutiques | os cruzeiros hidroacústicos de pesca
~ scientific trawl surveys | les campagnes de chalutage scientifique | os cruzeiros de arrasto científico
~ landings data | les données de débarquement | os dados de desembarque
illegal fishing | la pêche illicite | a pesca ilegal
fisheries economics | l'économie des pêches | a economia pesqueira
fishing communities | les communautés de pêcheurs | as comunidades pesqueiras
recreational fisheries | la pêche de loisir | a pesca amadora
elasmobranch conservation | la conservation des élasmobranches | a conservação de elasmobrânquios
tuna fisheries | les pêcheries thonières | as pescarias de atuns
demersal fish stocks | les stocks démersaux | os estoques demersais
small pelagic fish | les petits pélagiques | os pequenos peixes pelágicos
forage fish | les poissons fourrage | os peixes forrageiros
fishing gear innovation | l'innovation des engins de pêche | a inovação em petrechos de pesca
spatial fisheries closures | les fermetures spatiales de pêche | os fechamentos espaciais da pesca
~ shellfish sanitary monitoring | la surveillance sanitaire des coquillages | o monitoramento sanitário de moluscos
aquaculture site selection | le choix des sites aquacoles | a seleção de áreas aquícolas
fish farm benthic impacts | les impacts benthiques des fermes piscicoles | os impactos bentônicos de fazendas de peixes
fish stock collapse | l'effondrement des stocks | o colapso de estoques pesqueiros
~ data-limited stock assessment | l'évaluation des stocks pauvres en données | a avaliação de estoques com dados limitados
~ Bayesian stock models | les modèles bayésiens de dynamique des stocks | os modelos bayesianos de dinâmica de estoques
~ management strategy evaluation | l'évaluation des stratégies de gestion | a avaliação de estratégias de manejo
~ fish diet analysis | l'analyse des régimes alimentaires des poissons | a análise da dieta de peixes
nursery grounds | les zones de nourricerie | as áreas de berçário
~ fishing pressure indicators | les indicateurs de pression de pêche | os indicadores de pressão pesqueira
aquaculture feed ingredients | les ingrédients des aliments aquacoles | os ingredientes de rações aquícolas
oyster spat collection | le captage du naissain d'huîtres | a captação de sementes de ostras
mariculture development | le développement de la mariculture | o desenvolvimento da maricultura
~ catch reconstruction | la reconstruction des captures | a reconstrução de capturas
recruitment variability | la variabilité du recrutement | a variabilidade do recrutamento
"""

_PLANKTON = """
phytoplankton blooms | les efflorescences phytoplanctoniques | as florações de fitoplâncton
harmful algal blooms | les efflorescences algales toxiques | as florações de algas nocivas
primary production | la production primaire | a produção primária
~ chlorophyll fluorescence | la fluorescence chlorophyllienne | a fluorescência da clorofila
zooplankton grazing | le broutage du zooplancton | a herbivoria do zooplâncton
copepod population dynamics | la dynamique des populations de copépodes | a dinâmica populacional de copépodes
microbial loop | la boucle microbienne | a alça microbiana
nutrient limitation | la limitation en nutriments | a limitação por nutrientes
nitrogen cycling | le cycle de l'azote | a ciclagem do nitrogênio
phosphorus dynamics | la dynamique du phosphore | a dinâmica do fósforo
silicate depletion | l'épuisement des silicates | o esgotamento de silicato
carbon export | l'export de carbone | a exportação de carbono
biological carbon pump | la pompe biologique de carbone | a bomba biológica de carbono
dissolved organic matter | la matière organique dissoute | a matéria orgânica dissolvida
particulate organic carbon | le carbone organique particulaire | o carbono orgânico particulado
carbonate chemistry | la chimie des carbonates | a química do carbonato
ocean acidification | l'acidification des océans | a acidificação oceânica
oxygen minimum zones | les zones de minimum d'oxygène | as zonas de mínimo de oxigênio
coastal hypoxia | l'hypoxie côtière | a hipóxia costeira
coastal eutrophication | l'eutrophisation côtière | a eutrofização costeira
diatom communities | les communautés de diatomées | as comunidades de diatomáceas
dinoflagellate cysts | les kystes de dinoflagellés | os cistos de dinoflagelados
picophytoplankton abundance | l'abondance du picophytoplancton | a abundância do picofitoplâncton
phytoplankton phenology | la phénologie du phytoplancton | a fenologia do fitoplâncton
plankton community structure | la structure des communautés planctoniques | a estrutura das comunidades planctônicas
~ plankton imaging | l'imagerie du plancton | o imageamento do plâncton
~ flow cytometry | la cytométrie en flux | a citometria de fluxo
~ pigment analysis | l'analyse pigmentaire | a análise de pigmentos
nitrogen fixation | la fixation de l'azote | a fixação de nitrogênio
denitrification rates | les taux de dénitrification | as taxas de desnitrificação
benthic nutrient fluxes | les flux benthiques de nutriments | os fluxos bentônicos de nutrientes
sediment oxygen demand | la demande en oxygène du sédiment | a demanda de oxigênio do sedimento
air-sea CO2 exchange | les échanges de CO2 entre l'océan et l'atmosphère | as trocas de CO2 entre o oceano e a atmosfera
total alkalinity | l'alcalinité totale | a alcalinidade total
trace metal speciation | la spéciation des métaux traces | a especiação de metais-traço
iron limitation | la limitation par le fer | a limitação por ferro
marine viruses | les virus marins | os vírus marinhos
bacterioplankton diversity | la diversité du bactérioplancton | a diversidade do bacterioplâncton
mixotrophic protists | les protistes mixotrophes | os protistas mixotróficos
gelatinous zooplankton | le zooplancton gélatineux | o zooplâncton gelatinoso
jellyfish outbreaks | les proliférations de méduses | as proliferações de águas-vivas
sinking particle flux | le flux de particules sédimentantes | o fluxo de partículas em sedimentação
marine snow | la neige marine | a neve marinha
~ sediment traps | les pièges à particules | as armadilhas de sedimento
organic matter remineralisation | la reminéralisation de la matière organique | a remineralização da matéria orgânica
elemental stoichiometry | la stœchiométrie élémentaire | a estequiometria elementar
nutrient ratios | les rapports stœchiométriques des nutriments | as razões estequiométricas de nutrientes
~ biogeochemical modelling | la modélisation biogéochimique | a modelagem biogeoquímica
~ lower trophic level models | les modèles des bas niveaux trophiques | os modelos de níveis tróficos inferiores
plankton biodiversity | la biodiversité planctonique | a biodiversidade planctônica
phytoplankton size classes | les classes de taille du phytoplancton | as classes de tamanho do fitoplâncton
~ ocean colour algorithms | les algorithmes de couleur de l'océan | os algoritmos de cor do oceano
dissolved oxygen dynamics | la dynamique de l'oxygène dissous | a dinâmica do oxigênio dissolvido
nitrous oxide emissions | les émissions de protoxyde d'azote | as emissões de óxido nitroso
methane seepage | les suintements de méthane | as exsudações de metano
blue carbon | le carbone bleu | o carbono azul
carbon sequestration | la séquestration du carbone | o sequestro de carbono
phytoplankton photophysiology | la photophysiologie du phytoplancton | a fotofisiologia do fitoplâncton
light limitation | la limitation par la lumière | a limitação por luz
spring bloom timing | la date de l'efflorescence printanière | a época da floração de primavera
toxin production | la production de toxines | a produção de toxinas
microzooplankton herbivory | l'herbivorie du microzooplancton | a herbivoria do microzooplâncton
diel vertical migration | la migration verticale nycthémérale | a migração vertical nictemeral
upwelled nutrient supply | les apports de nutriments par upwelling | o aporte de nutrientes por ressurgência
~ isotopic tracers | les traceurs isotopiques | os traçadores isotópicos
silica cycle | le cycle de la silice | o ciclo da sílica
net community production | la production communautaire nette | a produção líquida da comunidade
carbon budgets | les bilans de carbone | os balanços de carbono
~ plankton time series | les séries temporelles de plancton | as séries temporais de plâncton
coccolithophore calcification | la calcification des coccolithophoridés | a calcificação de cocolitoforídeos
benthic-pelagic coupling | le couplage benthopélagique | o acoplamento bento-pelágico
"""

_HAZARDS = """
sea-level rise | l'élévation du niveau de la mer | a elevação do nível do mar
coastal flooding | la submersion marine | a inundação costeira
storm surges | les surcotes de tempête | as marés meteorológicas
extreme water levels | les niveaux d'eau extrêmes | os níveis d'água extremos
compound flooding | les inondations composées | as inundações compostas
wave overtopping | le franchissement par les vagues | o galgamento por ondas
~ coastal vulnerability index | l'indice de vulnérabilité côtière | o índice de vulnerabilidade costeira
shoreline retreat | le recul du trait de côte | o recuo da linha de costa
coastal erosion hazard | l'aléa érosion côtière | o perigo de erosão costeira
~ flood risk mapping | la cartographie du risque d'inondation | o mapeamento do risco de inundação
early warning systems | les systèmes d'alerte précoce | os sistemas de alerta precoce
tsunami inundation | l'inondation par tsunami | a inundação por tsunami
~ tsunami hazard assessment | l'évaluation de l'aléa tsunami | a avaliação do perigo de tsunami
~ return period estimation | l'estimation des périodes de retour | a estimativa de períodos de retorno
~ extreme value analysis | l'analyse des valeurs extrêmes | a análise de valores extremos
storm clustering | la succession des tempêtes | o agrupamento de tempestades
wave climate | le climat de houle | o clima de ondas
extreme wave heights | les hauteurs de vagues extrêmes | as alturas de onda extremas
coastal defence structures | les ouvrages de défense contre la mer | as estruturas de defesa costeira
seawall performance | la performance des murs de protection | o desempenho de muros de contenção
dyke breaching | la rupture des digues | o rompimento de diques
managed realignment | la dépoldérisation | o realinhamento gerenciado
nature-based solutions | les solutions fondées sur la nature | as soluções baseadas na natureza
coastal adaptation strategies | les stratégies d'adaptation côtière | as estratégias de adaptação costeira
relative sea-level change | la variation relative du niveau marin | a variação relativa do nível do mar
land subsidence | la subsidence des sols | a subsidência do terreno
vertical land motion | les mouvements verticaux du sol | os movimentos verticais do terreno
glacial isostatic adjustment | l'ajustement isostatique glaciaire | o ajuste isostático glacial
~ sea-level projections | les projections du niveau de la mer | as projeções do nível do mar
~ probabilistic flood modelling | la modélisation probabiliste des inondations | a modelagem probabilística de inundações
~ hydrodynamic flood models | les modèles hydrodynamiques de submersion | os modelos hidrodinâmicos de inundação
~ storm impact scale | l'échelle d'impact des tempêtes | a escala de impacto de tempestades
post-storm recovery | le rétablissement après tempête | a recuperação pós-tempestade
flood exposure | l'exposition aux inondations | a exposição a inundações
coastal risk perception | la perception du risque côtier | a percepção do risco costeiro
saltwater intrusion | l'intrusion saline | a intrusão salina
groundwater salinisation | la salinisation des nappes | a salinização de aquíferos
tropical cyclones | les cyclones tropicaux | os ciclones tropicais
extratropical storms | les tempêtes extratropicales | as tempestades extratropicais
meteotsunami events | les épisodes de météotsunami | os eventos de meteotsunami
landslide-generated waves | les vagues générées par glissement de terrain | as ondas geradas por deslizamentos
coastal landslides | les glissements de terrain côtiers | os deslizamentos costeiros
cliff collapse hazard | l'aléa effondrement de falaise | o perigo de colapso de falésias
~ flood damage functions | les fonctions de dommages liées aux inondations | as funções de dano por inundação
flood insurance | l'assurance contre les inondations | o seguro contra inundações
hazard zoning | le zonage des aléas | o zoneamento de perigos
coastal squeeze | le coincement côtier | o estrangulamento costeiro
emergency response planning | la planification des secours | o planejamento de resposta a emergências
climate change adaptation | l'adaptation au changement climatique | a adaptação às mudanças climáticas
~ multi-hazard assessment | l'évaluation multi-aléas | a avaliação multiperigos
flood defence failure | la défaillance des ouvrages de défense | a falha de defesas contra inundações
wave setup | la surélévation due aux vagues | a sobre-elevação por ondas
surge-tide interaction | l'interaction entre marée et surcote | a interação entre maré astronômica e maré meteorológica
~ historical storm reconstruction | la reconstruction des tempêtes historiques | a reconstrução de tempestades históricas
washover fans | les éventails de débordement | os leques de sobrelavagem
coastal resilience | la résilience côtière | a resiliência costeira
risk governance | la gouvernance des risques | a governança de riscos
~ flood forecasting | la prévision des submersions | a previsão de inundações
~ digital elevation models | les modèles numériques de terrain | os modelos digitais de elevação
overtopping discharge | le débit de franchissement | a vazão de galgamento
levee stability | la stabilité des digues | a estabilidade de diques marginais
estuarine flooding | les inondations estuariennes | as inundações estuarinas
storm tides | les marées de tempête | as marés de tempestade
erosion hotspots | les points chauds d'érosion | os pontos críticos de erosão
shoreline armouring | l'enrochement du littoral | o enrocamento do litoral
~ vulnerability mapping | la cartographie de la vulnérabilité | o mapeamento da vulnerabilidade
residual risk | le risque résiduel | o risco residual
~ sea-level rise scenarios | les scénarios d'élévation du niveau de la mer | os cenários de elevação do nível do mar
critical infrastructure exposure | l'exposition des infrastructures critiques | a exposição de infraestruturas críticas
adaptation pathways | les trajectoires d'adaptation | as trajetórias de adaptação
planned relocation | la relocalisation planifiée | a realocação planejada
"""

_OBSERVATION = """
~ satellite altimetry | l'altimétrie satellitaire | a altimetria por satélite
~ ocean colour remote sensing | la télédétection de la couleur de l'océan | o sensoriamento remoto da cor do oceano
~ synthetic aperture radar | le radar à synthèse d'ouverture | o radar de abertura sintética
sea surface salinity | la salinité de surface de la mer | a salinidade da superfície do mar
~ high-frequency radar | le radar haute fréquence | o radar de alta frequência
~ autonomous underwater vehicles | les véhicules sous-marins autonomes | os veículos subaquáticos autônomos
~ underwater gliders | les planeurs sous-marins | os planadores subaquáticos
~ profiling floats | les flotteurs profileurs | as boias perfiladoras
~ moored instruments | les instruments mouillés | os instrumentos fundeados
~ acoustic Doppler current profilers | les profileurs de courant à effet Doppler | os perfiladores acústicos de corrente por efeito Doppler
~ CTD profiles | les profils CTD | os perfis de CTD
~ multibeam echosounder surveys | les levés au sondeur multifaisceau | os levantamentos com ecobatímetro multifeixe
~ side-scan sonar | le sonar à balayage latéral | o sonar de varredura lateral
~ airborne lidar bathymetry | la bathymétrie lidar aéroportée | a batimetria por lidar aerotransportado
~ unmanned aerial vehicles | les drones aériens | os veículos aéreos não tripulados
~ drone photogrammetry | la photogrammétrie par drone | a fotogrametria por drone
~ hyperspectral imagery | l'imagerie hyperspectrale | as imagens hiperespectrais
~ multispectral imagery | l'imagerie multispectrale | as imagens multiespectrais
~ satellite-derived bathymetry | la bathymétrie dérivée des images satellitaires | a batimetria derivada de satélite
~ sensor calibration | l'étalonnage des capteurs | a calibração de sensores
~ observing system design | la conception des systèmes d'observation | o projeto de sistemas de observação
~ biogeochemical sensors | les capteurs biogéochimiques | os sensores biogeoquímicos
~ passive acoustic monitoring | la surveillance acoustique passive | o monitoramento acústico passivo
~ underwater acoustics | l'acoustique sous-marine | a acústica submarina
~ image classification | la classification d'images | a classificação de imagens
~ deep learning segmentation | la segmentation par apprentissage profond | a segmentação por aprendizado profundo
~ gap filling of time series | le comblement des lacunes des séries temporelles | o preenchimento de falhas em séries temporais
~ cloud masking | le masquage des nuages | o mascaramento de nuvens
~ atmospheric correction | la correction atmosphérique | a correção atmosférica
~ radiometric validation | la validation radiométrique | a validação radiométrica
~ significant wave height retrieval | l'estimation de la hauteur significative des vagues | a estimativa da altura significativa de onda
~ scatterometer winds | les vents mesurés par diffusiomètre | os ventos medidos por escaterômetro
~ surface current mapping | la cartographie des courants de surface | o mapeamento de correntes superficiais
~ sea ice monitoring | le suivi de la glace de mer | o monitoramento do gelo marinho
~ thermal infrared imagery | l'imagerie infrarouge thermique | as imagens no infravermelho termal
~ in situ validation | la validation in situ | a validação in situ
~ citizen science observations | les observations de sciences participatives | as observações de ciência cidadã
~ coastal observatories | les observatoires côtiers | os observatórios costeiros
~ real-time data transmission | la transmission de données en temps réel | a transmissão de dados em tempo real
~ biofouling mitigation | la lutte contre les salissures biologiques | o controle da bioincrustação
~ sensor networks | les réseaux de capteurs | as redes de sensores
~ data quality control | le contrôle qualité des données | o controle de qualidade de dados
~ open data infrastructures | les infrastructures de données ouvertes | as infraestruturas de dados abertos
~ metadata standards | les standards de métadonnées | os padrões de metadados
~ Argo floats | les flotteurs Argo | as boias Argo
~ water column imaging | l'imagerie de la colonne d'eau | o imageamento da coluna d'água
~ seabed classification | la classification des fonds marins | a classificação do fundo marinho
~ acoustic seabed mapping | la cartographie acoustique des fonds | o mapeamento acústico do fundo marinho
~ optical backscatter sensors | les capteurs de rétrodiffusion optique | os sensores de retroespalhamento óptico
~ turbidity monitoring | le suivi de la turbidité | o monitoramento da turbidez
~ shoreline detection algorithms | les algorithmes de détection du trait de côte | os algoritmos de detecção da linha de costa
~ coastal video systems | les systèmes vidéo côtiers | os sistemas de vídeo costeiros
~ GNSS reflectometry | la réflectométrie GNSS | a refletometria GNSS
~ tide gauge networks | les réseaux marégraphiques | as redes maregráficas
~ ship-based surveys | les campagnes océanographiques en mer | os cruzeiros oceanográficos
~ ocean forecasting systems | les systèmes de prévision océanique | os sistemas de previsão oceânica
~ ocean digital twins | les jumeaux numériques de l'océan | os gêmeos digitais do oceano
~ spectral wave buoys | les bouées houlographes | as boias ondógrafas direcionais
sea surface temperature | la température de surface de la mer | a temperatura da superfície do mar
inherent optical properties | les propriétés optiques inhérentes | as propriedades ópticas inerentes
~ suspended matter retrieval | l'estimation des matières en suspension | a estimativa do material particulado em suspensão
~ altimetry waveform retracking | le retraitement des formes d'onde altimétriques | o reprocessamento de formas de onda altimétricas
~ wide-swath altimetry | l'altimétrie à large fauchée | a altimetria de faixa larga
radar backscatter | la rétrodiffusion radar | o retroespalhamento radar
~ change detection | la détection de changements | a detecção de mudanças
~ photogrammetric surveys | les levés photogrammétriques | os levantamentos fotogramétricos
~ terrestrial laser scanning | la lasergrammétrie terrestre | a varredura a laser terrestre
~ uncrewed surface vehicles | les drones de surface | os veículos de superfície não tripulados
~ habitat mapping | la cartographie des habitats | o mapeamento de habitats
~ mooring arrays | les réseaux de mouillages | os arranjos de fundeios
~ data assimilation | l'assimilation de données | a assimilação de dados
"""

_POLLUTION = """
microplastic pollution | la pollution par les microplastiques | a poluição por microplásticos
microplastic ingestion | l'ingestion de microplastiques | a ingestão de microplásticos
nanoplastic toxicity | la toxicité des nanoplastiques | a toxicidade dos nanoplásticos
marine litter | les déchets marins | o lixo marinho
~ beach litter surveys | les relevés de déchets sur les plages | os levantamentos de lixo em praias
plastic debris transport | le transport des débris plastiques | o transporte de detritos plásticos
~ polymer identification | l'identification des polymères | a identificação de polímeros
~ infrared spectroscopy | la spectroscopie infrarouge | a espectroscopia no infravermelho
persistent organic pollutants | les polluants organiques persistants | os poluentes orgânicos persistentes
polycyclic aromatic hydrocarbons | les hydrocarbures aromatiques polycycliques | os hidrocarbonetos policíclicos aromáticos
oil spills | les marées noires | os derramamentos de petróleo
~ oil spill modelling | la modélisation des marées noires | a modelagem de derramamentos de petróleo
heavy metal contamination | la contamination par les métaux lourds | a contaminação por metais pesados
mercury bioaccumulation | la bioaccumulation du mercure | a bioacumulação de mercúrio
trace metal contamination | la contamination par les éléments traces métalliques | a contaminação por metais-traço
emerging contaminants | les contaminants émergents | os contaminantes emergentes
pharmaceutical residues | les résidus pharmaceutiques | os resíduos de fármacos
pesticide runoff | le ruissellement de pesticides | o escoamento de agrotóxicos
antifouling biocides | les biocides antisalissures | os biocidas anti-incrustantes
organotin compounds | les composés organostanniques | os compostos organoestânicos
endocrine disruptors | les perturbateurs endocriniens | os desreguladores endócrinos
~ ecotoxicological bioassays | les bioessais écotoxicologiques | os bioensaios ecotoxicológicos
~ biomarker responses | les réponses des biomarqueurs | as respostas de biomarcadores
~ sentinel species | les espèces sentinelles | as espécies sentinelas
~ mussel watch programmes | les programmes de biosurveillance par les moules | os programas de biomonitoramento com mexilhões
sediment contamination | la contamination des sédiments | a contaminação de sedimentos
dredged sediment management | la gestion des sédiments de dragage | a gestão de sedimentos dragados
wastewater discharges | les rejets d'eaux usées | os lançamentos de efluentes
faecal contamination | la contamination fécale | a contaminação fecal
bathing water quality | la qualité des eaux de baignade | a balneabilidade
nutrient pollution | la pollution par les nutriments | a poluição por nutrientes
agricultural runoff | le ruissellement agricole | o escoamento agrícola
plastic additives | les additifs plastiques | os aditivos plásticos
textile microfibres | les microfibres textiles | as microfibras têxteis
plastic weathering | le vieillissement des plastiques | o intemperismo de plásticos
biofilm colonisation | la colonisation par les biofilms | a colonização por biofilmes
plastisphere communities | les communautés de la plastisphère | as comunidades da plastisfera
riverine plastic fluxes | les flux fluviaux de plastiques | os fluxos fluviais de plástico
floating debris accumulation | l'accumulation de débris flottants | o acúmulo de detritos flutuantes
underwater noise pollution | la pollution sonore sous-marine | a poluição sonora submarina
artificial light pollution | la pollution lumineuse | a poluição luminosa
thermal discharges | les rejets thermiques | os lançamentos térmicos
~ radionuclide tracers | les traceurs radioactifs | os traçadores radioativos
~ chemical risk assessment | l'évaluation du risque chimique | a avaliação de risco químico
~ toxicokinetic modelling | la modélisation toxicocinétique | a modelagem toxicocinética
oxidative stress | le stress oxydant | o estresse oxidativo
trophic transfer of contaminants | le transfert trophique des contaminants | a transferência trófica de contaminantes
~ passive samplers | les échantillonneurs passifs | os amostradores passivos
~ non-target screening | le criblage non ciblé | a triagem não direcionada
~ high-resolution mass spectrometry | la spectrométrie de masse à haute résolution | a espectrometria de massas de alta resolução
PFAS contamination | la contamination par les PFAS | a contaminação por PFAS
shipwreck pollution | la pollution liée aux épaves | a poluição por naufrágios
ghost fishing gear | les engins de pêche fantômes | os petrechos de pesca fantasma
plastic pollution policy | les politiques de lutte contre la pollution plastique | as políticas de combate à poluição plástica
~ pollution source apportionment | l'attribution des sources de pollution | a atribuição de fontes de poluição
sewage outfalls | les émissaires d'eaux usées | os emissários submarinos de esgoto
~ contaminant fate modelling | la modélisation du devenir des contaminants | a modelagem do destino de contaminantes
sediment quality guidelines | les critères de qualité des sédiments | os valores-guia de qualidade de sedimentos
chemical mixtures | les mélanges chimiques | as misturas químicas
hydrocarbon biodegradation | la biodégradation des hydrocarbures | a biodegradação de hidrocarbonetos
oil dispersants | les dispersants pétroliers | os dispersantes de petróleo
~ microplastic sampling methods | les méthodes d'échantillonnage des microplastiques | os métodos de amostragem de microplásticos
~ neuston nets | les filets à neuston | as redes de nêuston
~ litter drift modelling | la modélisation de la dérive des déchets | a modelagem da deriva de lixo
environmental quality standards | les normes de qualité environnementale | os padrões de qualidade ambiental
antibiotic resistance genes | les gènes de résistance aux antibiotiques | os genes de resistência a antibióticos
pathogenic vibrios | les vibrions pathogènes | os víbrios patogênicos
~ pollution monitoring networks | les réseaux de surveillance de la pollution | as redes de monitoramento da poluição
cumulative impacts | les impacts cumulés | os impactos cumulativos
harmful algal blooms | les efflorescences algales toxiques | as florações de algas nocivas
trace metal speciation | la spéciation des métaux traces | a especiação de metais-traço
underwater radiated noise | le bruit rayonné sous l'eau | o ruído irradiado submarino
"""

_GOVERNANCE = """
integrated coastal zone management | la gestion intégrée des zones côtières | o gerenciamento costeiro integrado
marine spatial planning | la planification de l'espace maritime | o planejamento espacial marinho
coastal governance | la gouvernance côtière | a governança costeira
stakeholder participation | la participation des parties prenantes | a participação das partes interessadas
local ecological knowledge | les savoirs écologiques locaux | o conhecimento ecológico local
community-based management | la gestion communautaire | a gestão comunitária
co-management arrangements | les arrangements de cogestion | os arranjos de cogestão
environmental justice | la justice environnementale | a justiça ambiental
coastal livelihoods | les moyens de subsistance littoraux | os meios de vida costeiros
social vulnerability | la vulnérabilité sociale | a vulnerabilidade social
place attachment | l'attachement au lieu | o apego ao lugar
~ risk perception surveys | les enquêtes de perception du risque | as pesquisas de percepção de risco
policy instruments | les instruments d'action publique | os instrumentos de política pública
maritime law | le droit maritime | o direito marítimo
law of the sea | le droit de la mer | o direito do mar
marine protected area governance | la gouvernance des aires marines protégées | a governança de áreas marinhas protegidas
social-ecological systems | les systèmes socio-écologiques | os sistemas socioecológicos
adaptive governance | la gouvernance adaptative | a governança adaptativa
collective action | l'action collective | a ação coletiva
commons management | la gestion des communs | a gestão dos bens comuns
coastal tourism | le tourisme littoral | o turismo costeiro
second-home development | le développement des résidences secondaires | a expansão das segundas residências
coastal urbanisation | l'urbanisation du littoral | a urbanização do litoral
land-use conflicts | les conflits d'usage | os conflitos de uso do solo
planned relocation | la relocalisation planifiée | a realocação planejada
climate-related migration | les migrations climatiques | as migrações climáticas
heritage landscapes | les paysages patrimoniaux | as paisagens patrimoniais
maritime heritage | le patrimoine maritime | o patrimônio marítimo
environmental history | l'histoire environnementale | a história ambiental
ocean literacy | la culture océanique | a cultura oceânica
science-policy interface | l'interface science-politique | a interface ciência-política
knowledge co-production | la coproduction des savoirs | a coprodução de conhecimento
deliberative democracy | la démocratie délibérative | a democracia deliberativa
public consultation | la concertation publique | a consulta pública
spatial planning | l'aménagement du territoire | o ordenamento territorial
coastal land tenure | le foncier littoral | a posse da terra no litoral
fishers' knowledge | les savoirs des pêcheurs | o conhecimento dos pescadores
customary marine tenure | les régimes fonciers marins coutumiers | os regimes consuetudinários de posse marinha
environmental conflicts | les conflits environnementaux | os conflitos ambientais
social acceptance of offshore wind | l'acceptabilité sociale de l'éolien en mer | a aceitação social da energia eólica offshore
media coverage | la couverture médiatique | a cobertura midiática
policy mobilities | la circulation des politiques publiques | a circulação de políticas públicas
transboundary cooperation | la coopération transfrontalière | a cooperação transfronteiriça
ocean governance | la gouvernance des océans | a governança oceânica
blue justice | la justice bleue | a justiça azul
adaptation pathways | les trajectoires d'adaptation | as trajetórias de adaptação
~ willingness to pay | le consentement à payer | a disposição a pagar
~ ecosystem services valuation | l'évaluation des services écosystémiques | a valoração de serviços ecossistêmicos
cultural ecosystem services | les services écosystémiques culturels | os serviços ecossistêmicos culturais
environmental education | l'éducation à l'environnement | a educação ambiental
policy implementation gaps | les écarts de mise en œuvre des politiques | as lacunas de implementação de políticas
multilevel governance | la gouvernance multiniveau | a governança multinível
legal pluralism | le pluralisme juridique | o pluralismo jurídico
property rights regimes | les régimes de droits de propriété | os regimes de direitos de propriedade
territorial identity | l'identité territoriale | a identidade territorial
disaster memory | la mémoire des catastrophes | a memória de desastres
power relations | les rapports de pouvoir | as relações de poder
coastal risk perception | la perception du risque côtier | a percepção do risco costeiro
risk governance | la gouvernance des risques | a governança de riscos
fishing communities | les communautés de pêcheurs | as comunidades pesqueiras
coastal adaptation strategies | les stratégies d'adaptation côtière | as estratégias de adaptação costeira
shoreline access rights | l'accès au rivage | o direito de acesso à orla
coastal gentrification | la gentrification littorale | a gentrificação costeira
island communities | les communautés insulaires | as comunidades insulares
fisheries governance | la gouvernance des pêches | a governança pesqueira
marine conservation conflicts | les conflits liés à la conservation marine | os conflitos de conservação marinha
coastal planning law | le droit de l'urbanisme littoral | a legislação de planejamento costeiro
community resilience | la résilience des communautés | a resiliência comunitária
tourism carrying capacity | la capacité de charge touristique | a capacidade de carga turística
maritime boundaries | les frontières maritimes | as fronteiras marítimas
"""

_BLUE_ECONOMY = """
port logistics | la logistique portuaire | a logística portuária
container shipping | le transport maritime conteneurisé | o transporte marítimo de contêineres
port competitiveness | la compétitivité portuaire | a competitividade portuária
hinterland connectivity | la desserte de l'arrière-pays | a conectividade com a hinterlândia
maritime transport emissions | les émissions du transport maritime | as emissões do transporte marítimo
shipping decarbonisation | la décarbonation du transport maritime | a descarbonização do transporte marítimo
alternative marine fuels | les carburants marins alternatifs | os combustíveis marítimos alternativos
ballast water management | la gestion des eaux de ballast | a gestão da água de lastro
port expansion | l'extension portuaire | a expansão portuária
dredging operations | les opérations de dragage | as operações de dragagem
navigation channels | les chenaux de navigation | os canais de navegação
ship traffic density | la densité du trafic maritime | a densidade do tráfego marítimo
~ AIS data analysis | l'analyse des données AIS | a análise de dados AIS
maritime safety | la sécurité maritime | a segurança marítima
offshore wind energy | l'énergie éolienne en mer | a energia eólica offshore
floating wind turbines | les éoliennes flottantes | as turbinas eólicas flutuantes
tidal stream energy | l'énergie hydrolienne | a energia das correntes de maré
wave energy converters | les systèmes houlomoteurs | os conversores de energia das ondas
marine renewable energy | les énergies marines renouvelables | as energias renováveis marinhas
offshore wind farm impacts | les impacts des parcs éoliens en mer | os impactos de parques eólicos offshore
subsea cables | les câbles sous-marins | os cabos submarinos
deep-sea mining | l'exploitation minière des grands fonds | a mineração em mar profundo
marine biotechnology | la biotechnologie marine | a biotecnologia marinha
blue growth strategies | les stratégies de croissance bleue | as estratégias de crescimento azul
maritime clusters | les clusters maritimes | os clusters marítimos
shipbuilding industry | l'industrie de la construction navale | a indústria da construção naval
cruise tourism | le tourisme de croisière | o turismo de cruzeiros
port-city relations | les relations ville-port | as relações porto-cidade
waterfront redevelopment | la reconversion des fronts d'eau | a revitalização de orlas urbanas
port governance | la gouvernance portuaire | a governança portuária
maritime spatial conflicts | les conflits d'usage en mer | os conflitos de uso do espaço marinho
~ ocean economy accounts | les comptes de l'économie maritime | as contas da economia do mar
~ input-output analysis | l'analyse entrées-sorties | a análise de insumo-produto
~ cost-benefit analysis | l'analyse coûts-avantages | a análise custo-benefício
~ natural capital accounting | la comptabilité du capital naturel | a contabilidade do capital natural
seaweed value chains | les filières des algues | as cadeias de valor das algas
port state control | le contrôle par l'État du port | o controle pelo Estado do porto
shipping routes | les routes maritimes | as rotas marítimas
Arctic shipping | la navigation arctique | a navegação no Ártico
vessel speed reduction | la réduction de la vitesse des navires | a redução da velocidade das embarcações
ship strikes on whales | les collisions entre navires et cétacés | as colisões de navios com baleias
port water quality | la qualité des eaux portuaires | a qualidade da água portuária
maritime labour | le travail maritime | o trabalho marítimo
seafarer welfare | le bien-être des marins | o bem-estar dos marítimos
freight flows | les flux de fret | os fluxos de carga
short sea shipping | le transport maritime à courte distance | a navegação de cabotagem
intermodal transport | le transport intermodal | o transporte intermodal
port resilience | la résilience portuaire | a resiliência portuária
port climate adaptation | l'adaptation des ports au changement climatique | a adaptação climática dos portos
offshore decommissioning | le démantèlement des installations en mer | o descomissionamento de instalações offshore
coastal tourism economics | l'économie du tourisme littoral | a economia do turismo costeiro
recreational boating | la navigation de plaisance | a náutica de recreio
marina development | l'aménagement des ports de plaisance | a implantação de marinas
ocean energy policy | les politiques énergétiques maritimes | as políticas de energia oceânica
~ environmental impact assessment | l'étude d'impact environnemental | o estudo de impacto ambiental
hull biofouling | les salissures biologiques des coques | a bioincrustação de cascos
green port initiatives | les démarches de ports verts | as iniciativas de portos verdes
shore power | l'alimentation électrique à quai | o fornecimento de energia elétrica no cais
liquefied natural gas bunkering | le soutage au gaz naturel liquéfié | o abastecimento com gás natural liquefeito
supply chain disruptions | les perturbations des chaînes d'approvisionnement | as rupturas das cadeias de suprimento
maritime economics | l'économie maritime | a economia marítima
maritime employment | l'emploi maritime | o emprego marítimo
port authority strategies | les stratégies des autorités portuaires | as estratégias das autoridades portuárias
offshore wind supply chain | la chaîne d'approvisionnement de l'éolien en mer | a cadeia de suprimentos da energia eólica offshore
~ blue economy indicators | les indicateurs de l'économie bleue | os indicadores da economia azul
fisheries economics | l'économie des pêches | a economia pesqueira
coastal tourism | le tourisme littoral | o turismo costeiro
~ willingness to pay | le consentement à payer | a disposição a pagar
~ ecosystem services valuation | l'évaluation des services écosystémiques | a valoração de serviços ecossistêmicos
underwater radiated noise | le bruit rayonné sous l'eau | o ruído irradiado submarino
marine aggregate extraction | l'extraction de granulats marins | a extração de agregados marinhos
artificial reefs | les récifs artificiels | os recifes artificiais
"""

_ESTUARIES = """
salt marsh accretion | l'accrétion des marais salés | a acreção de marismas
tidal marsh vegetation | la végétation des marais tidaux | a vegetação de marismas de maré
mangrove forests | les forêts de mangroves | as florestas de manguezal
mangrove carbon stocks | les stocks de carbone des mangroves | os estoques de carbono dos manguezais
estuarine turbidity maximum | le bouchon vaseux | a zona de máxima turbidez estuarina
salt wedge dynamics | la dynamique du coin salé | a dinâmica da cunha salina
mudflat erosion | l'érosion des vasières | a erosão de planícies lamosas
fine sediment trapping | le piégeage des sédiments fins | o aprisionamento de sedimentos finos
marsh edge erosion | l'érosion des bordures de marais | a erosão das bordas de marismas
tidal channel networks | les réseaux de chenaux de marée | as redes de canais de maré
estuarine morphodynamics | la morphodynamique estuarienne | a morfodinâmica estuarina
river discharge variability | la variabilité des débits fluviaux | a variabilidade da vazão fluvial
wetland restoration | la restauration des zones humides | a restauração de áreas úmidas
coastal lagoons | les lagunes côtières | as lagunas costeiras
lagoon inlet stability | la stabilité des graus | a estabilidade das barras lagunares
brackish water ecology | l'écologie des eaux saumâtres | a ecologia de águas salobras
halophyte communities | les communautés d'halophytes | as comunidades de halófitas
sediment accretion rates | les taux d'accrétion sédimentaire | as taxas de acreção sedimentar
marsh elevation dynamics | la dynamique altimétrique des marais | a dinâmica altimétrica de marismas
~ surface elevation tables | les tables d'élévation de surface | as tabelas de elevação de superfície
carbon burial | l'enfouissement du carbone | o soterramento de carbono
wetland biogeochemistry | la biogéochimie des zones humides | a biogeoquímica de áreas úmidas
estuarine fish nurseries | les nourriceries estuariennes | os berçários estuarinos de peixes
fish passage restoration | le rétablissement de la continuité écologique | a restauração da passagem de peixes
diadromous fish | les poissons amphihalins | os peixes diádromos
eel migration | la migration des anguilles | a migração de enguias
waterbird habitats | les habitats des oiseaux d'eau | os habitats de aves aquáticas
shorebird feeding grounds | les zones d'alimentation des limicoles | as áreas de alimentação de aves limícolas
polder management | la gestion des polders | a gestão de pôlderes
tidal reconnection | la reconnexion tidale | a reconexão com a maré
estuarine water quality | la qualité des eaux estuariennes | a qualidade da água estuarina
estuarine hypoxia | l'hypoxie estuarienne | a hipóxia estuarina
mangrove dieback | le dépérissement des mangroves | a mortalidade de manguezais
reed beds | les roselières | os caniçais
wetland hydrology | l'hydrologie des zones humides | a hidrologia de áreas úmidas
groundwater-surface water exchange | les échanges entre nappe et eaux de surface | as trocas entre água subterrânea e superficial
estuarine sediment budgets | les bilans sédimentaires estuariens | os balanços sedimentares estuarinos
tidal wave propagation | la propagation de l'onde de marée | a propagação da onda de maré
tidal bore | le mascaret | a pororoca
haline stratification | la stratification haline | a estratificação halina
fluid mud layers | les couches de crème de vase | as camadas de lama fluida
estuarine ecosystem health | la santé des écosystèmes estuariens | a saúde dos ecossistemas estuarinos
wetland loss | la disparition des zones humides | a perda de áreas úmidas
landward marsh migration | la migration des marais vers l'intérieur des terres | a migração de marismas em direção ao continente
marsh resilience to sea-level rise | la résilience des marais face à l'élévation du niveau marin | a resiliência de marismas à elevação do nível do mar
mangrove propagule dispersal | la dispersion des propagules de mangrove | a dispersão de propágulos de mangue
bivalve filtration | la filtration par les bivalves | a filtração por bivalves
estuarine food webs | les réseaux trophiques estuariens | as teias tróficas estuarinas
nutrient retention | la rétention des nutriments | a retenção de nutrientes
wetland ecosystem services | les services écosystémiques des zones humides | os serviços ecossistêmicos de áreas úmidas
river-estuary continuum | le continuum fleuve-estuaire | o contínuo rio-estuário
sediment starvation | le déficit sédimentaire | o déficit sedimentar
delta subsidence | la subsidence des deltas | a subsidência de deltas
deltaic wetlands | les zones humides deltaïques | as áreas úmidas deltaicas
salt pans | les marais salants | as salinas
tidal freshwater marshes | les marais d'eau douce soumis à la marée | as marismas de água doce sob influência de maré
~ wetland mapping | la cartographie des zones humides | o mapeamento de áreas úmidas
vegetation-flow interactions | les interactions entre végétation et écoulement | as interações entre vegetação e escoamento
wave attenuation by vegetation | l'atténuation de la houle par la végétation | a atenuação de ondas pela vegetação
mangrove restoration | la restauration des mangroves | a restauração de manguezais
lagoon eutrophication | l'eutrophisation lagunaire | a eutrofização lagunar
macrotidal estuaries | les estuaires macrotidaux | os estuários de macromaré
microtidal lagoons | les lagunes microtidales | as lagunas de micromaré
estuarine mixing | le mélange estuarien | a mistura estuarina
estuarine circulation | la circulation estuarienne | a circulação estuarina
blue carbon ecosystems | les écosystèmes de carbone bleu | os ecossistemas de carbono azul
saltmarsh grazing | le pâturage des prés salés | o pastejo em marismas
saltwater intrusion | l'intrusion saline | a intrusão salina
water residence time | le temps de résidence des eaux | o tempo de residência da água
nursery habitats | les habitats de nourricerie | os habitats de berçário
saltmarsh fauna | la faune des prés salés | a fauna de marismas
"""

_PALEO = """
~ sediment cores | les carottes sédimentaires | os testemunhos sedimentares
foraminiferal assemblages | les assemblages de foraminifères | as assembleias de foraminíferos
planktonic foraminifera | les foraminifères planctoniques | os foraminíferos planctônicos
benthic foraminifera | les foraminifères benthiques | os foraminíferos bentônicos
~ oxygen isotope stratigraphy | la stratigraphie isotopique de l'oxygène | a estratigrafia de isótopos de oxigênio
~ radiocarbon dating | la datation par le radiocarbone | a datação por radiocarbono
~ age-depth models | les modèles âge-profondeur | os modelos idade-profundidade
Holocene climate variability | la variabilité climatique holocène | a variabilidade climática do Holoceno
Last Glacial Maximum | le dernier maximum glaciaire | o Último Máximo Glacial
deglacial sea-level history | l'histoire du niveau marin à la déglaciation | a história do nível do mar na deglaciação
relative sea-level reconstruction | la reconstruction du niveau marin relatif | a reconstrução do nível relativo do mar
sea surface temperature reconstruction | la reconstruction des températures de surface | a reconstrução da temperatura da superfície do mar
~ alkenone palaeothermometry | la paléothermométrie des alcénones | a paleotermometria de alquenonas
~ magnesium-calcium thermometry | la thermométrie magnésium-calcium | a termometria magnésio-cálcio
~ diatom-based transfer functions | les fonctions de transfert fondées sur les diatomées | as funções de transferência baseadas em diatomáceas
marine pollen records | les enregistrements polliniques marins | os registros polínicos marinhos
coral climate archives | les archives climatiques coralliennes | os arquivos climáticos em corais
~ bivalve sclerochronology | la sclérochronologie des bivalves | a esclerocronologia de bivalves
~ marine tephrochronology | la téphrochronologie marine | a tefrocronologia marinha
marine isotope stages | les stades isotopiques marins | os estágios isotópicos marinhos
ocean ventilation changes | les changements de ventilation océanique | as mudanças na ventilação oceânica
~ past circulation proxies | les traceurs de la circulation passée | os indicadores da circulação pretérita
~ neodymium isotopes | les isotopes du néodyme | os isótopos de neodímio
ice-rafted debris | les débris transportés par les glaces | os detritos transportados por gelo
abrupt climate events | les événements climatiques abrupts | os eventos climáticos abruptos
Heinrich events | les événements de Heinrich | os eventos Heinrich
millennial-scale variability | la variabilité millénaire | a variabilidade em escala milenar
orbital forcing | le forçage orbital | o forçamento orbital
~ paleoproductivity proxies | les indicateurs de paléoproductivité | os indicadores de paleoprodutividade
biogenic silica | la silice biogène | a sílica biogênica
sediment geochemistry | la géochimie sédimentaire | a geoquímica sedimentar
~ X-ray fluorescence core scanning | l'analyse des carottes par fluorescence X | a varredura de testemunhos por fluorescência de raios X
varved sediments | les sédiments varvés | os sedimentos varvados
paleostorm records | les archives de paléotempêtes | os registros de paleotempestades
paleotsunami deposits | les dépôts de paléotsunamis | os depósitos de paleotsunamis
coastal barrier evolution | l'évolution des barrières côtières | a evolução de barreiras costeiras
Holocene sea-level highstand | le haut niveau marin holocène | o máximo transgressivo do Holoceno
submerged landscapes | les paysages submergés | as paisagens submersas
~ seismic stratigraphy | la stratigraphie sismique | a estratigrafia sísmica
continental margin sedimentation | la sédimentation sur les marges continentales | a sedimentação nas margens continentais
turbidite records | les enregistrements de turbidites | os registros de turbiditos
contourite drifts | les dépôts contouritiques | os depósitos contorníticos
glacial-interglacial cycles | les cycles glaciaires-interglaciaires | os ciclos glacial-interglacial
carbon cycle feedbacks | les rétroactions du cycle du carbone | as retroalimentações do ciclo do carbono
deep water formation | la formation des eaux profondes | a formação de águas profundas
~ proxy calibration | l'étalonnage des traceurs paléoclimatiques | a calibração de indicadores paleoclimáticos
~ model-data comparison | la comparaison entre modèles et données | a comparação entre modelos e dados
~ paleoclimate modelling | la modélisation paléoclimatique | a modelagem paleoclimática
~ Bayesian age modelling | la modélisation bayésienne des âges | a modelagem bayesiana de idades
~ lipid biomarkers | les biomarqueurs lipidiques | os biomarcadores lipídicos
~ GDGT-based palaeothermometry | la paléothermométrie fondée sur les GDGT | a paleotermometria baseada em GDGT
dinocyst assemblages | les assemblages de dinokystes | as assembleias de dinocistos
coccolith records | les enregistrements de coccolithes | os registros de cocólitos
sediment provenance | la provenance des sédiments | a proveniência sedimentar
palaeoenvironmental reconstruction | la reconstruction paléoenvironnementale | a reconstrução paleoambiental
estuarine infilling history | l'histoire du comblement estuarien | a história do preenchimento estuarino
Little Ice Age | le petit âge glaciaire | a Pequena Idade do Gelo
Medieval Climate Anomaly | l'anomalie climatique médiévale | a Anomalia Climática Medieval
monsoon variability | la variabilité de la mousson | a variabilidade das monções
ENSO variability | la variabilité ENSO | a variabilidade do ENOS
sea ice reconstruction | la reconstruction de la glace de mer | a reconstrução do gelo marinho
marine terraces | les terrasses marines | os terraços marinhos
~ uranium-series dating | la datation par les séries de l'uranium | a datação por séries de urânio
~ optically stimulated luminescence | la luminescence stimulée optiquement | a luminescência opticamente estimulada
beachrock formation | la formation des grès de plage | a formação de rochas de praia
shell middens | les amas coquilliers | os sambaquis
coastal geoarchaeology | la géoarchéologie littorale | a geoarqueologia costeira
deep-sea coral geochemistry | la géochimie des coraux profonds | a geoquímica de corais de profundidade
~ palaeoceanographic proxies | les indicateurs paléocéanographiques | os indicadores paleoceanográficos
dinoflagellate cysts | les kystes de dinoflagellés | os cistos de dinoflagelados
glacial isostatic adjustment | l'ajustement isostatique glaciaire | o ajuste isostático glacial
"""

# ---------------------------------------------------------------------------
# Compound terms: families of aspects and objects
# ---------------------------------------------------------------------------
# Real subfields name many specific things: a species and one of its traits,
# an instrument and a processing step. Each family below lists aspects, then
# "--", then objects (English modifier | French with article | Portuguese with
# article); every pair gives one term, English "<object> <aspect>", French
# "<aspect> de <object>" and Portuguese "<aspect> de <object>" (``hake
# biomass``, ``la biomasse du merlu``, ``a biomassa da merluza``). "=="
# separates families; a leading "~" on an aspect makes its terms techniques.

_GEOMORPHOLOGY_FAMILIES = """
erosion | l'érosion | a erosão
morphology | la morphologie | a morfologia
evolution | l'évolution | a evolução
stability | la stabilité | a estabilidade
sediment budget | le bilan sédimentaire | o balanço sedimentar
volume changes | les variations de volume | as variações de volume
~ monitoring | le suivi | o monitoramento
~ mapping | la cartographie | o mapeamento
vulnerability | la vulnérabilité | a vulnerabilidade
resilience | la résilience | a resiliência
dynamics | la dynamique | a dinâmica
topography | la topographie | a topografia
sediment supply | les apports sédimentaires | o aporte sedimentar
~ surveys | les levés | os levantamentos
~ modelling | la modélisation | a modelagem
--
beach | les plages | as praias
dune | les dunes | as dunas
cliff | les falaises | as falésias
barrier | les cordons littoraux | as barreiras costeiras
spit | les flèches sableuses | os pontais arenosos
sandbar | les barres sableuses | os bancos arenosos
shoreface | l'avant-côte | a face litorânea
foredune | les avant-dunes | as dunas frontais
embayment | les anses | as enseadas
tombolo | les tombolos | os tômbolos
shingle beach | les plages de galets | as praias de seixos
chenier | les cheniers | os cheniers
coastal plain | les plaines côtières | as planícies costeiras
berm | les bermes | as bermas
nearshore bar | les barres d'avant-côte | os bancos da antepraia
tidal delta | les deltas de marée | os deltas de maré
beach ridge plain | les plaines de cordons | as planícies de cordões litorâneos
barrier island | les îles-barrières | as ilhas-barreira
pocket beach | les plages de poche | as praias de bolso
coastal dune field | les champs de dunes littoraux | os campos de dunas costeiras
rocky shore | les estrans rocheux | os costões rochosos
headland | les caps | os promontórios
delta front | le front de delta | a frente deltaica
sand bank | les bancs de sable | os bancos de areia
upper beach | les hauts de plage | a pós-praia superior
dune slack | les pannes dunaires | as depressões interdunares
gravel spit | les flèches de galets | os pontais de cascalho
==
grain size | la granulométrie | a granulometria
mineralogy | la minéralogie | a mineralogia
~ sampling | l'échantillonnage | a amostragem
sorting | le tri | a seleção
provenance | la provenance | a proveniência
~ tracing | le traçage | o rastreamento
--
beach sand | les sables de plage | as areias de praia
dune sand | les sables dunaires | as areias de duna
gravel | les galets | os cascalhos
shelf sediment | les sédiments du plateau | os sedimentos de plataforma
mud | les vases | as lamas
tidal flat sediment | les sédiments d'estran | os sedimentos de planície de maré
river sediment | les sédiments fluviaux | os sedimentos fluviais
aeolian sand | les sables éoliens | as areias eólicas
shell hash | les débris coquilliers | os fragmentos de conchas
carbonate sand | les sables carbonatés | as areias carbonáticas
==
bedforms | les figures sédimentaires | as formas de fundo
roughness | la rugosité | a rugosidade
permeability | la perméabilité | a permeabilidade
infiltration | l'infiltration | a infiltração
compaction | la compaction | a compactação
--
swash zone | la zone de jet de rive | a zona de espraiamento
beach step | la marche de plage | o degrau praial
gravel berm | les bermes de galets | as bermas de cascalho
intertidal bar | les barres intertidales | os bancos intermareais
runnel | les bâches | as calhas praiais
backshore | l'arrière-plage | a pós-praia
"""

_CIRCULATION_FAMILIES = """
variability | la variabilité | a variabilidade
seasonal cycle | le cycle saisonnier | o ciclo sazonal
vertical structure | la structure verticale | a estrutura vertical
energetics | l'énergétique | a energética
forcing mechanisms | les mécanismes de forçage | os mecanismos de forçamento
~ modelling | la modélisation | a modelagem
dynamics | la dynamique | a dinâmica
intensity | l'intensité | a intensidade
persistence | la persistance | a persistência
transport | le transport | o transporte
~ observations | les observations | as observações
~ parameterisation | la paramétrisation | a parametrização
--
shelf current | les courants de plateau | as correntes de plataforma
slope current | les courants de pente | as correntes de talude
tidal current | les courants de marée | as correntes de maré
coastal current | les courants côtiers | as correntes costeiras
river plume | les panaches fluviaux | as plumas fluviais
upwelling | l'upwelling | a ressurgência
eddy | les tourbillons | os vórtices
internal wave | les ondes internes | as ondas internas
mixed layer | la couche de mélange | a camada de mistura
front | les fronts | as frentes
boundary current | les courants de bord | as correntes de contorno
bottom boundary layer | la couche limite de fond | a camada limite de fundo
thermocline | la thermocline | a termoclina
halocline | l'halocline | a haloclina
shelf break front | le front de talus | a frente de quebra de plataforma
tidal jet | les jets de marée | os jatos de maré
wind-driven current | les courants de dérive | as correntes de deriva pelo vento
overflow | les débordements d'eau profonde | os transbordamentos de águas profundas
recirculation gyre | les gyres de recirculation | os giros de recirculação
trapped wave | les ondes piégées | as ondas aprisionadas
surface current | les courants de surface | as correntes superficiais
undercurrent | les sous-courants | as subcorrentes
plume front | les fronts de panache | as frentes de pluma
tidal inlet | les passes tidales | as desembocaduras de maré
Rossby wave | les ondes de Rossby | as ondas de Rossby
cold pool | les masses d'eau froide de fond | as massas d'água frias de fundo
shelf break | le rebord du plateau | a quebra da plataforma
near-inertial wave | les ondes quasi inertielles | as ondas quase inerciais
Ekman layer | la couche d'Ekman | a camada de Ekman
bottom current | les courants de fond | as correntes de fundo
==
heat budget | le bilan de chaleur | o balanço de calor
salt budget | le bilan de sel | o balanço de sal
~ mooring observations | les observations par mouillage | as observações por fundeio
exchange flow | les échanges | as trocas de água
flushing | le renouvellement des eaux | a renovação das águas
stratification | la stratification | a estratificação
--
bay | les baies | as baías
fjord | les fjords | os fiordes
shelf sea | les mers de plateau | os mares de plataforma
marginal sea | les mers bordières | os mares marginais
strait | les détroits | os estreitos
gulf | les golfes | os golfos
==
shear | le cisaillement | o cisalhamento
vorticity | la vorticité | a vorticidade
dissipation | la dissipation | a dissipação
entrainment | l'entraînement | o entranhamento
~ tracking | le suivi | o rastreamento
--
density current | les courants de densité | as correntes de densidade
gravity current | les courants de gravité | as correntes de gravidade
cross-shelf flow | les écoulements transversaux | os escoamentos através da plataforma
sill overflow | les débordements de seuil | os transbordamentos sobre soleiras
tidal eddy | les tourbillons de marée | os vórtices de maré
headland eddy | les tourbillons de cap | os vórtices de promontório
"""

_ECOLOGY_FAMILIES = """
distribution | la distribution | a distribuição
abundance | l'abondance | a abundância
recruitment | le recrutement | o recrutamento
growth | la croissance | o crescimento
feeding ecology | l'écologie trophique | a ecologia alimentar
habitat use | l'utilisation de l'habitat | o uso de habitat
~ monitoring | le suivi | o monitoramento
behaviour | le comportement | o comportamento
physiology | la physiologie | a fisiologia
reproduction | la reproduction | a reprodução
diet | le régime alimentaire | a dieta
mortality | la mortalité | a mortalidade
thermal tolerance | la tolérance thermique | a tolerância térmica
~ tagging | le marquage | a marcação
--
mussel | les moules | os mexilhões
sea urchin | les oursins | os ouriços-do-mar
seabird | les oiseaux marins | as aves marinhas
harbour seal | les phoques veaux-marins | as focas-comuns
reef fish | les poissons récifaux | os peixes recifais
polychaete | les polychètes | os poliquetas
amphipod | les amphipodes | os anfípodes
limpet | les patelles | as lapas
sea star | les étoiles de mer | as estrelas-do-mar
crab | les crabes | os caranguejos
shrimp | les crevettes | os camarões
cuttlefish | les seiches | os chocos
sea turtle | les tortues marines | as tartarugas marinhas
dolphin | les dauphins | os golfinhos
shorebird | les limicoles | as aves limícolas
juvenile fish | les poissons juvéniles | os peixes juvenis
bivalve | les bivalves | os bivalves
gastropod | les gastéropodes | os gastrópodes
sea anemone | les anémones de mer | as anêmonas-do-mar
brittle star | les ophiures | os ofiuroides
barnacle | les balanes | as cracas
grey seal | les phoques gris | as focas-cinzentas
sandeel | les lançons | as galeotas
cephalopod | les céphalopodes | os cefalópodes
octopus | les poulpes | os polvos
holothurian | les holothuries | os pepinos-do-mar
hermit crab | les bernard-l'ermite | os ermitões
seahorse | les hippocampes | os cavalos-marinhos
sea squirt | les ascidies | as ascídias
tube worm | les vers tubicoles | os vermes tubícolas
whale | les baleines | as baleias
porpoise | les marsouins | as toninhas
tern | les sternes | os trinta-réis
cormorant | les cormorans | os biguás
==
cover | le recouvrement | a cobertura
productivity | la productivité | a produtividade
fragmentation | la fragmentation | a fragmentação
~ mapping | la cartographie | o mapeamento
restoration | la restauration | a restauração
resilience | la résilience | a resiliência
decline | le déclin | o declínio
~ monitoring | le suivi | o monitoramento
--
seagrass | les herbiers | as fanerógamas marinhas
kelp | les laminaires | as laminárias
coral | les coraux | os corais
maerl bed | les bancs de maërl | os bancos de algas calcárias
coralligenous reef | le coralligène | o coralígeno
rhodolith bed | les bancs de rhodolithes | os bancos de rodolitos
macroalgal canopy | la canopée de macroalgues | o dossel de macroalgas
turf algae | les gazons algaux | os tapetes de algas filamentosas
==
breeding success | le succès reproducteur | o sucesso reprodutivo
foraging range | le rayon d'alimentation | a área de forrageamento
colony size | la taille des colonies | o tamanho das colônias
migration timing | la phénologie migratoire | a fenologia migratória
diving behaviour | le comportement de plongée | o comportamento de mergulho
--
puffin | les macareux | os papagaios-do-mar
gannet | les fous de Bassan | os atobás
kittiwake | les mouettes tridactyles | as gaivotas-tridáctilas
shearwater | les puffins | as pardelas
fur seal | les otaries à fourrure | os lobos-marinhos
guillemot | les guillemots | os airos
"""

_FISHERIES_FAMILIES = """
biomass | la biomasse | a biomassa
landings | les débarquements | os desembarques
recruitment | le recrutement | o recrutamento
spatial distribution | la distribution spatiale | a distribuição espacial
growth | la croissance | o crescimento
exploitation status | l'état d'exploitation | o estado de exploração
~ abundance indices | les indices d'abondance | os índices de abundância
catch | les captures | as capturas
size structure | la structure en taille | a estrutura de tamanhos
maturity | la maturité | a maturidade
diet | le régime alimentaire | a dieta
discards | les rejets | os descartes
~ tagging | le marquage | a marcação
--
cod | le cabillaud | o bacalhau
hake | le merlu | a merluza
sole | la sole | o linguado
anchovy | l'anchois | a anchoíta
sardine | la sardine | a sardinha
mackerel | le maquereau | a cavala
sea bass | le bar | o robalo-europeu
plaice | la plie | a solha
Norway lobster | la langoustine | o lagostim
tuna | le thon | o atum
red mullet | le rouget | o salmonete
whiting | le merlan | o merlango
monkfish | la baudroie | o tamboril
herring | le hareng | o arenque
squid | le calmar | a lula
sprat | le sprat | a espadilha
pollack | le lieu jaune | a juliana
horse mackerel | le chinchard | o carapau
spider crab | l'araignée de mer | a santola
brown crab | le tourteau | a sapateira
common cuttlefish | la seiche commune | o choco-comum
skate | les raies | as raias
black sea bream | la dorade grise | a choupa
blue whiting | le merlan bleu | o verdinho
John Dory | le saint-pierre | o peixe-galo
saithe | le lieu noir | o escamudo
haddock | l'églefin | a arinca
European eel | l'anguille européenne | a enguia-europeia
swordfish | l'espadon | o espadarte
albacore | le germon | a albacora
==
mortality | la mortalité | a mortalidade
growth performance | les performances de croissance | o desempenho de crescimento
disease resistance | la résistance aux maladies | a resistência a doenças
welfare | le bien-être | o bem-estar
feeding | l'alimentation | a alimentação
~ selective breeding | la sélection génétique | o melhoramento genético
--
Pacific oyster | l'huître creuse | a ostra-do-pacífico
blue mussel | la moule commune | o mexilhão-azul
gilthead sea bream | la dorade royale | a dourada
abalone | l'ormeau | o abalone
sea cucumber | l'holothurie | o pepino-do-mar
Atlantic salmon | le saumon atlantique | o salmão-do-atlântico
turbot | le turbot | o pregado
flat oyster | l'huître plate | a ostra-plana
clam | la palourde | o vôngole
meagre | le maigre | a corvina
==
catchability | la capturabilité | a capturabilidade
selectivity | la sélectivité | a seletividade
bycatch rates | les taux de captures accessoires | as taxas de captura incidental
fuel efficiency | l'efficacité énergétique | a eficiência energética
seabed contact | le contact avec le fond | o contato com o fundo
--
otter trawl | les chaluts à panneaux | as redes de arrasto com portas
beam trawl | les chaluts à perche | as redes de arrasto de vara
gillnet | les filets maillants | as redes de emalhar
longline | les palangres | os espinhéis
purse seine | les sennes coulissantes | as redes de cerco
pot fishery | les pêcheries aux casiers | as pescarias com armadilhas
"""

_PLANKTON_FAMILIES = """
abundance | l'abondance | a abundância
biomass | la biomasse | a biomassa
growth rates | les taux de croissance | as taxas de crescimento
grazing losses | les pertes par broutage | as perdas por herbivoria
seasonal succession | la succession saisonnière | a sucessão sazonal
~ time series | les séries temporelles | as séries temporais
size structure | la structure en taille | a estrutura de tamanhos
diversity | la diversité | a diversidade
respiration | la respiration | a respiração
vertical distribution | la distribution verticale | a distribuição vertical
~ counts | les dénombrements | as contagens
--
diatom | les diatomées | as diatomáceas
dinoflagellate | les dinoflagellés | os dinoflagelados
coccolithophore | les coccolithophoridés | os cocolitoforídeos
copepod | les copépodes | os copépodes
cyanobacteria | les cyanobactéries | as cianobactérias
ciliate | les ciliés | os ciliados
krill | le krill | o krill
appendicularian | les appendiculaires | as apendiculárias
picoeukaryote | les picoeucaryotes | os picoeucariotos
radiolarian | les radiolaires | os radiolários
heterotrophic bacteria | les bactéries hétérotrophes | as bactérias heterotróficas
nanoflagellate | les nanoflagellés | os nanoflagelados
salp | les salpes | as salpas
pteropod | les ptéropodes | os pterópodes
mysid | les mysidacés | os misidáceos
rotifer | les rotifères | os rotíferos
haptophyte | les haptophytes | as haptófitas
cryptophyte | les cryptophytes | as criptófitas
Pseudo-nitzschia | les Pseudo-nitzschia | as Pseudo-nitzschia
Alexandrium | les Alexandrium | os Alexandrium
Chaetoceros | les Chaetoceros | as Chaetoceros
Skeletonema | les Skeletonema | as Skeletonema
Dinophysis | les Dinophysis | os Dinophysis
Calanus | les Calanus | os Calanus
Oithona | les Oithona | os Oithona
Synechococcus | les Synechococcus | as Synechococcus
Prochlorococcus | les Prochlorococcus | as Prochlorococcus
Emiliania huxleyi | les Emiliania huxleyi | os Emiliania huxleyi
==
uptake | l'assimilation | a assimilação
distribution | la distribution | a distribuição
budget | le bilan | o balanço
~ measurements | les mesures | as medições
cycling | le cycle | a ciclagem
fluxes | les flux | os fluxos
limitation | la limitation | a limitação
--
nitrate | les nitrates | o nitrato
ammonium | l'ammonium | o amônio
phosphate | les phosphates | o fosfato
silicic acid | l'acide silicique | o ácido silícico
dissolved iron | le fer dissous | o ferro dissolvido
dissolved inorganic carbon | le carbone inorganique dissous | o carbono inorgânico dissolvido
organic nitrogen | l'azote organique | o nitrogênio orgânico
urea | l'urée | a ureia
nitrite | les nitrites | o nitrito
dissolved organic phosphorus | le phosphore organique dissous | o fósforo orgânico dissolvido
cobalt | le cobalt | o cobalto
manganese | le manganèse | o manganês
==
sinking velocity | la vitesse de chute | a velocidade de afundamento
aggregation | l'agrégation | a agregação
degradation rates | les taux de dégradation | as taxas de degradação
carbon content | la teneur en carbone | o teor de carbono
~ imaging | l'imagerie | o imageamento
--
faecal pellet | les pelotes fécales | as pelotas fecais
phytodetritus | les phytodétritus | os fitodetritos
transparent exopolymer | les exopolymères transparents | os exopolímeros transparentes
diatom aggregate | les agrégats de diatomées | os agregados de diatomáceas
zooplankton carcass | les carcasses de zooplancton | as carcaças de zooplâncton
mineral ballast | le lest minéral | o lastro mineral
"""

_HAZARDS_FAMILIES = """
exposure | l'exposition | a exposição
vulnerability | la vulnérabilité | a vulnerabilidade
damage | l'endommagement | os danos
adaptation | l'adaptation | a adaptação
relocation | la relocalisation | a realocação
~ vulnerability assessment | le diagnostic de vulnérabilité | o diagnóstico de vulnerabilidade
resilience | la résilience | a resiliência
insurance | l'assurance | o seguro
protection | la protection | a proteção
flood risk | le risque de submersion | o risco de inundação
~ damage assessment | l'évaluation des dommages | a avaliação de danos
--
coastal housing | les habitations littorales | as moradias costeiras
port infrastructure | les infrastructures portuaires | as infraestruturas portuárias
coastal road | les routes côtières | as rodovias costeiras
campsite | les campings | os campings
coastal farmland | les terres agricoles littorales | as terras agrícolas costeiras
tourist resort | les stations balnéaires | os balneários turísticos
coastal wastewater plant | les stations d'épuration littorales | as estações de tratamento de esgoto costeiras
coastal airport | les aéroports littoraux | os aeroportos costeiros
heritage site | les sites patrimoniaux | os sítios patrimoniais
harbour town | les villes portuaires | as cidades portuárias
coastal railway | les voies ferrées littorales | as ferrovias costeiras
drinking water supply | l'alimentation en eau potable | o abastecimento de água potável
coastal hospital | les hôpitaux littoraux | os hospitais costeiros
energy network | les réseaux d'énergie | as redes de energia
coastal village | les villages littoraux | as vilas costeiras
mobile home park | les parcs résidentiels de loisirs | os parques de casas móveis
fish farm | les fermes aquacoles | as fazendas aquícolas
industrial zone | les zones industrielles littorales | as zonas industriais costeiras
seaside promenade | les promenades de front de mer | os calçadões à beira-mar
marina | les ports de plaisance | as marinas
grazed salt marsh | les prés salés pâturés | as marismas pastejadas
==
return levels | les niveaux de retour | os níveis de retorno
frequency | la fréquence | a frequência
intensity | l'intensité | a intensidade
trends | les tendances | as tendências
~ hindcast | la simulation rétrospective | a simulação retrospectiva
impacts | les impacts | os impactos
~ forecasting | la prévision | a previsão
--
storm surge | les surcotes | as marés meteorológicas
extreme wave | les vagues extrêmes | as ondas extremas
coastal flood | les submersions marines | as inundações costeiras
river flood | les crues | as cheias fluviais
storm | les tempêtes | as tempestades
tsunami | les tsunamis | os tsunamis
extreme rainfall | les pluies extrêmes | as chuvas extremas
coastal erosion event | les épisodes d'érosion | os eventos de erosão costeira
compound event | les événements composés | os eventos compostos
high tide flood | les inondations de marée haute | as inundações de maré alta
swell event | les épisodes de houle | os eventos de ondulação
==
failure modes | les modes de rupture | os modos de falha
design standards | les normes de conception | os critérios de projeto
maintenance costs | les coûts d'entretien | os custos de manutenção
crest level | la cote d'arase | a cota de coroamento
~ inspection | l'inspection | a inspeção
--
rubble mound | les digues à talus | os quebra-mares de enrocamento
sea dyke | les digues maritimes | os diques marítimos
groyne | les épis | os espigões
breakwater | les brise-lames | os quebra-mares
tidal barrier | les barrages anti-marée | as barreiras contra marés
flood gate | les portes à flot | as comportas contra inundações
"""

_OBSERVATION_FAMILIES = """
~ calibration | l'étalonnage | a calibração
~ validation | la validation | a validação
~ data processing | le traitement des données | o processamento de dados
~ deployment | le déploiement | o lançamento
~ uncertainty | les incertitudes | as incertezas
~ maintenance | la maintenance | a manutenção
~ intercomparison | l'intercomparaison | a intercomparação
~ data quality | la qualité des données | a qualidade dos dados
~ sampling strategy | la stratégie d'échantillonnage | a estratégia de amostragem
~ energy autonomy | l'autonomie énergétique | a autonomia energética
--
glider | les planeurs | os planadores
Argo float | les flotteurs Argo | as boias perfiladoras Argo
HF radar | les radars HF | os radares HF
ADCP | les courantomètres ADCP | os perfiladores ADCP
satellite altimeter | les altimètres satellitaires | os altímetros por satélite
tide gauge | les marégraphes | os marégrafos
wave buoy | les bouées houlographes | as boias ondógrafas
lidar | le lidar | o lidar
multibeam echosounder | les sondeurs multifaisceaux | os ecobatímetros multifeixe
CTD sensor | les capteurs CTD | os sensores CTD
underwater camera | les caméras sous-marines | as câmeras subaquáticas
hydrophone | les hydrophones | os hidrofones
drifter | les bouées dérivantes | os derivadores
echosounder | les échosondeurs | as ecossondas
moored buoy | les bouées ancrées | as boias fundeadas
ocean colour sensor | les capteurs de couleur de l'océan | os sensores de cor do oceano
turbidity sensor | les turbidimètres | os turbidímetros
oxygen optode | les optodes à oxygène | os optodos de oxigênio
pH sensor | les capteurs de pH | os sensores de pH
scatterometer | les diffusiomètres | os escaterômetros
drone camera | les caméras de drones | as câmeras de drones
fluorometer | les fluorimètres | os fluorímetros
radiometer | les radiomètres | os radiômetros
water sampler | les préleveurs d'eau | os amostradores de água
benthic lander | les stations benthiques autonomes | os módulos bentônicos autônomos
current meter | les courantomètres | os correntômetros
thermistor chain | les chaînes de thermistances | as cadeias de termistores
acoustic release | les largueurs acoustiques | os liberadores acústicos
gravity corer | les carottiers par gravité | os testemunhadores por gravidade
Niskin bottle | les bouteilles Niskin | as garrafas de Niskin
sonar | les sonars | os sonares
towed camera | les caméras tractées | as câmeras rebocadas
surface buoy | les bouées de surface | as boias de superfície
wave glider | les planeurs de surface | os planadores de ondas
==
~ gridding | le maillage | a interpolação em grade
~ interpolation | l'interpolation | a interpolação
~ gap filling | le comblement des lacunes | o preenchimento de falhas
~ downscaling | la descente d'échelle | a regionalização
~ archiving | l'archivage | o arquivamento
--
altimetry product | les produits altimétriques | os produtos altimétricos
ocean colour product | les produits de couleur de l'océan | os produtos de cor do oceano
reanalysis field | les champs de réanalyse | os campos de reanálise
glider transect | les sections de planeurs | as seções de planadores
mooring record | les séries de mouillage | as séries de fundeio
radar current map | les cartes de courants radar | os mapas de correntes por radar
"""

_POLLUTION_FAMILIES = """
toxicity | la toxicité | a toxicidade
bioavailability | la biodisponibilité | a biodisponibilidade
environmental fate | le devenir environnemental | o destino ambiental
sources | les sources | as fontes
~ analysis | le dosage | a dosagem
bioaccumulation | la bioaccumulation | a bioacumulação
degradation | la dégradation | a degradação
exposure | l'exposition | a exposição
~ monitoring | la surveillance | o monitoramento
~ risk assessment | l'évaluation des risques | a avaliação de riscos
--
copper | le cuivre | o cobre
cadmium | le cadmium | o cádmio
lead | le plomb | o chumbo
zinc | le zinc | o zinco
arsenic | l'arsenic | o arsênio
PCB | les PCB | os PCB
glyphosate | le glyphosate | o glifosato
tyre particle | les particules de pneus | as partículas de pneus
flame retardant | les retardateurs de flamme | os retardantes de chama
UV filter | les filtres UV | os filtros UV
mercury | le mercure | o mercúrio
nickel | le nickel | o níquel
chromium | le chrome | o cromo
silver nanoparticle | les nanoparticules d'argent | as nanopartículas de prata
triclosan | le triclosan | o triclosan
bisphenol A | le bisphénol A | o bisfenol A
phthalate | les phtalates | os ftalatos
diclofenac | le diclofénac | o diclofenaco
microplastic fibre | les fibres microplastiques | as fibras microplásticas
caffeine | la caféine | a cafeína
antibiotic | les antibiotiques | os antibióticos
hydrocarbon | les hydrocarbures | os hidrocarbonetos
tributyltin | le tributylétain | o tributilestanho
PFOS | le PFOS | o PFOS
dioxin | les dioxines | as dioxinas
carbamazepine | la carbamazépine | a carbamazepina
polystyrene | le polystyrène | o poliestireno
brominated compound | les composés bromés | os compostos bromados
rare earth element | les terres rares | os elementos terras-raras
radiocaesium | le césium radioactif | o césio radioativo
==
contamination | la contamination | a contaminação
~ biomonitoring | la biosurveillance | o biomonitoramento
~ sampling | l'échantillonnage | a amostragem
--
oyster | les huîtres | as ostras
mussel | les moules | os mexilhões
fish muscle | la chair des poissons | o músculo de peixes
seabird egg | les œufs d'oiseaux marins | os ovos de aves marinhas
harbour sediment | les sédiments portuaires | os sedimentos portuários
beach sand | les sables de plage | as areias de praia
sea turtle | les tortues marines | as tartarugas marinhas
marine mammal | les mammifères marins | os mamíferos marinhos
zooplankton | le zooplancton | o zooplâncton
seagrass | les herbiers | as fanerógamas marinhas
cockle | les coques | os berbigões
flatfish | les poissons plats | os peixes chatos
seafood | les produits de la mer | os frutos do mar
estuarine sediment | les sédiments estuariens | os sedimentos estuarinos
==
genotoxicity | la génotoxicité | a genotoxicidade
neurotoxicity | la neurotoxicité | a neurotoxicidade
immunotoxicity | l'immunotoxicité | a imunotoxicidade
reproductive effects | les effets sur la reproduction | os efeitos reprodutivos
gene expression | l'expression génique | a expressão gênica
--
oyster larvae | les larves d'huîtres | as larvas de ostras
sea urchin embryo | les embryons d'oursins | os embriões de ouriços-do-mar
copepod nauplii | les nauplii de copépodes | os náuplios de copépodes
fish embryo | les embryons de poissons | os embriões de peixes
mussel haemocyte | les hémocytes de moules | os hemócitos de mexilhões
microalgal culture | les cultures de microalgues | as culturas de microalgas
"""

_GOVERNANCE_FAMILIES = """
governance | la gouvernance | a governança
regulation | la réglementation | a regulamentação
use conflicts | les conflits d'usage | os conflitos de uso
planning | la planification | o planejamento
perceptions | les perceptions | as percepções
~ stakeholder mapping | la cartographie des acteurs | o mapeamento de atores
management | la gestion | a gestão
valuation | la valorisation | a valorização
access rights | les droits d'accès | os direitos de acesso
social uses | les usages sociaux | os usos sociais
--
shellfish farming | la conchyliculture | a malacocultura
beach | les plages | as praias
marine reserve | les réserves marines | as reservas marinhas
estuary | les estuaires | os estuários
coastal wetland | les zones humides littorales | as áreas úmidas costeiras
island | les îles | as ilhas
fishing ground | les zones de pêche | os pesqueiros
seaside town | les villes balnéaires | as cidades balneárias
harbour | les ports | os portos
dune system | les systèmes dunaires | os sistemas de dunas
bay | les baies | as baías
lagoon | les lagunes | as lagunas
offshore wind zone | les zones d'éolien en mer | as zonas de energia eólica offshore
coastal path | les sentiers littoraux | as trilhas costeiras
mooring area | les zones de mouillage | as áreas de fundeio
salt marsh | les prés salés | as marismas
river mouth | les embouchures | as fozes de rios
marine park | les parcs marins | os parques marinhos
coastal commune | les communes littorales | os municípios costeiros
protected coastline | les littoraux protégés | os litorais protegidos
oyster basin | les bassins ostréicoles | as áreas de cultivo de ostras
coastal forest | les forêts littorales | as florestas costeiras
offshore island | les îles du large | as ilhas oceânicas
tourist beach | les plages touristiques | as praias turísticas
wild coast | les côtes sauvages | os litorais selvagens
==
history | l'histoire | a história
heritage | la patrimonialisation | a patrimonialização
memory | la mémoire | a memória
tourism | la mise en tourisme | a turistificação
representations | les représentations | as representações
--
fishing port | les ports de pêche | os portos pesqueiros
lighthouse | les phares | os faróis
seaside resort | les stations balnéaires | os balneários
salt pan | les salines | as salinas
oyster farming | l'ostréiculture | a ostreicultura
shipwreck | les naufrages | os naufrágios
harbour district | les quartiers portuaires | os bairros portuários
fishing village | les villages de pêcheurs | as vilas de pescadores
coastal fortification | les fortifications littorales | as fortificações costeiras
sea bathing | les bains de mer | os banhos de mar
sea rescue | le sauvetage en mer | o salvamento marítimo
naval dockyard | les arsenaux | os arsenais navais
==
access | l'accès | o acesso
privatisation | la privatisation | a privatização
commodification | la marchandisation | a mercantilização
--
shoreline | le rivage | a orla
seafront | le front de mer | a orla marítima urbana
sea view | la vue sur mer | a vista para o mar
coastal land | le foncier littoral | as terras costeiras
==
legal status | le statut juridique | o estatuto jurídico
enforcement | l'application | a fiscalização
zoning | le zonage | o zoneamento
compensation schemes | les mécanismes de compensation | os mecanismos de compensação
public inquiry | l'enquête publique | a consulta pública
--
public maritime domain | le domaine public maritime | os terrenos de marinha
coastal setback | la bande littorale inconstructible | a faixa de proteção costeira
fishing licence | les licences de pêche | as licenças de pesca
mooring concession | les concessions de mouillage | as concessões de fundeio
beach concession | les concessions de plage | as concessões de praia
aquaculture lease | les concessions conchylicoles | as cessões de áreas aquícolas
"""

_BLUE_ECONOMY_FAMILIES = """
economics | l'économie | a economia
market | le marché | o mercado
value chain | la chaîne de valeur | a cadeia de valor
employment | l'emploi | o emprego
innovation | l'innovation | a inovação
~ cost assessment | l'évaluation des coûts | a avaliação de custos
regulation | la régulation | a regulação
competitiveness | la compétitivité | a competitividade
public funding | le financement public | o financiamento público
sustainability | la durabilité | a sustentabilidade
~ economic modelling | la modélisation économique | a modelagem econômica
--
offshore wind | l'éolien en mer | a energia eólica marinha
tidal energy | l'énergie marémotrice | a energia maremotriz
seaweed farming | l'algoculture | o cultivo de algas
container port | les ports à conteneurs | os portos de contêineres
cruise industry | la croisière | a indústria de cruzeiros
ferry service | les services de ferry | os serviços de balsa
shipyard | les chantiers navals | os estaleiros
fishing fleet | les flottilles de pêche | as frotas pesqueiras
marina | les ports de plaisance | as marinas
wave energy | l'énergie houlomotrice | a energia das ondas
marine aquaculture | l'aquaculture marine | a aquicultura marinha
seafood processing | la transformation des produits de la mer | o beneficiamento de pescado
yachting | la grande plaisance | a náutica de luxo
offshore oil | le pétrole en mer | o petróleo offshore
dredging industry | l'industrie du dragage | a indústria de dragagem
ship recycling | le recyclage des navires | a reciclagem de navios
marine tourism | le tourisme maritime | o turismo marítimo
salt production | la production de sel | a produção de sal
ship repair | la réparation navale | a reparação naval
marine insurance | l'assurance maritime | o seguro marítimo
seaweed biorefinery | le bioraffinage des algues | a biorrefinaria de algas
fish auction | les criées | os leilões de pescado
shipping line | les compagnies maritimes | as companhias de navegação
coastal hotel | l'hôtellerie littorale | a hotelaria costeira
water sports | les sports nautiques | os esportes náuticos
marine equipment | les équipements maritimes | os equipamentos marítimos
==
emissions | les émissions | as emissões
traffic | le trafic | o tráfego
~ monitoring | la surveillance | o monitoramento
safety | la sécurité | a segurança
fuel consumption | la consommation de carburant | o consumo de combustível
--
cruise ship | les navires de croisière | os navios de cruzeiro
container ship | les porte-conteneurs | os navios porta-contêineres
tanker | les pétroliers | os navios-tanque
ferry | les ferries | as balsas
fishing vessel | les navires de pêche | as embarcações pesqueiras
bulk carrier | les vraquiers | os graneleiros
LNG carrier | les méthaniers | os navios metaneiros
offshore supply vessel | les navires de servitude | os navios de apoio offshore
car carrier | les transporteurs de véhicules | os navios transportadores de veículos
research vessel | les navires océanographiques | os navios oceanográficos
tugboat | les remorqueurs | os rebocadores
==
throughput | le débit | a movimentação de carga
automation | l'automatisation | a automação
congestion | la congestion | o congestionamento
energy use | la consommation d'énergie | o consumo de energia
labour relations | les relations de travail | as relações de trabalho
--
container terminal | les terminaux à conteneurs | os terminais de contêineres
ro-ro terminal | les terminaux rouliers | os terminais ro-ro
bulk terminal | les terminaux vraquiers | os terminais de granéis
dry port | les ports secs | os portos secos
cruise terminal | les terminaux de croisière | os terminais de cruzeiros
ferry terminal | les gares maritimes | os terminais de balsas
"""

_ESTUARIES_FAMILIES = """
sedimentation | la sédimentation | a sedimentação
hydrodynamics | l'hydrodynamique | a hidrodinâmica
vegetation | la végétation | a vegetação
ecology | l'écologie | a ecologia
restoration | la restauration | a restauração
morphology | la morphologie | a morfologia
~ monitoring | le suivi | o monitoramento
biogeochemistry | la biogéochimie | a biogeoquímica
carbon storage | le stockage de carbone | o estoque de carbono
biodiversity | la biodiversité | a biodiversidade
water quality | la qualité de l'eau | a qualidade da água
~ mapping | la cartographie | o mapeamento
--
salt marsh | les prés salés | as marismas
mudflat | les vasières | as planícies lamosas
tidal channel | les chenaux de marée | os canais de maré
mangrove | les mangroves | os manguezais
lagoon | les lagunes | as lagunas
reed bed | les roselières | os caniçais
tidal flat | les estrans | as planícies de maré
polder | les polders | os pôlderes
estuary mouth | les embouchures | as desembocaduras estuarinas
backbarrier marsh | les marais d'arrière-cordon | as marismas de retrobarreira
coastal pond | les étangs littoraux | as lagoas costeiras
freshwater marsh | les marais doux | os brejos de água doce
delta plain | les plaines deltaïques | as planícies deltaicas
tidal creek | les chenaux secondaires | os canais secundários de maré
sandflat | les estrans sableux | as planícies arenosas de maré
salt meadow | les herbus | os prados salinos
brackish marsh | les marais saumâtres | as marismas salobras
floodplain | les plaines d'inondation | as planícies de inundação
coastal wetland | les zones humides littorales | as áreas úmidas costeiras
marsh creek | les chenaux de marais | os canais de marisma
estuarine beach | les plages estuariennes | as praias estuarinas
tidal wetland | les zones humides tidales | as áreas úmidas de maré
mangrove creek | les chenaux de mangrove | os canais de manguezal
brackish lagoon | les lagunes saumâtres | as lagunas salobras
==
salinity | la salinité | a salinidade
turbidity | la turbidité | a turbidez
nutrient loads | les apports de nutriments | as cargas de nutrientes
~ water sampling | les prélèvements d'eau | a coleta de água
residence time | le temps de résidence | o tempo de residência
oxygen levels | l'oxygénation | a oxigenação
--
estuary | les estuaires | os estuários
lagoon | les lagunes | as lagunas
delta | les deltas | os deltas
tidal river | les fleuves soumis à la marée | os rios sob influência de maré
coastal bay | les baies côtières | as baías costeiras
==
root biomass | la biomasse racinaire | a biomassa radicular
above-ground productivity | la productivité aérienne | a produtividade aérea
decomposition | la décomposition | a decomposição
zonation | la zonation | a zonação
invasion | l'invasion | a invasão
--
Spartina | les spartines | as espartinas
Salicornia | les salicornes | as salicórnias
sea purslane | l'obione | a beldroega-do-mar
reed | les roseaux | os caniços
rush | les joncs | os juncos
sedge | les laîches | as ciperáceas
"""

_PALEO_FAMILIES = """
records | les enregistrements | os registros
stratigraphy | la stratigraphie | a estratigrafia
~ dating | la datation | a datação
~ geochemistry | la géochimie | a geoquímica
~ sampling | l'échantillonnage | a amostragem
chronology | la chronologie | a cronologia
palaeoecology | la paléoécologie | a paleoecologia
~ radiocarbon ages | les âges radiocarbone | as idades radiocarbono
~ isotope analysis | l'analyse isotopique | a análise isotópica
--
marine core | les carottes marines | os testemunhos marinhos
fossil coral | les coraux fossiles | os corais fósseis
mollusc shell | les coquilles de mollusques | as conchas de moluscos
foraminifera | les foraminifères | os foraminíferos
tephra layer | les niveaux de téphra | as camadas de tefra
coastal peat | les tourbes littorales | as turfas costeiras
beach ridge | les anciens cordons de plage | os antigos cordões litorâneos
lagoon sediment | les sédiments lagunaires | os sedimentos lagunares
fossil reef | les récifs fossiles | os recifes fósseis
sediment drift | les dérives sédimentaires | os corpos de deriva sedimentar
coral microatoll | les micro-atolls coralliens | os microatóis de coral
salt marsh core | les carottes de marais salés | os testemunhos de marisma
coastal dune sequence | les séquences dunaires littorales | as sequências de dunas costeiras
deep-sea core | les carottes profondes | os testemunhos de mar profundo
estuarine core | les carottes estuariennes | os testemunhos estuarinos
midden | les amas coquilliers | os sambaquis
raised beach | les plages soulevées | as praias soerguidas
submerged forest | les forêts submergées | as florestas submersas
sabkha | les sebkhas | os sabkhas
coastal lake | les lacs littoraux | os lagos costeiros
shelf core | les carottes du plateau | os testemunhos de plataforma
fossil oyster bank | les bancs d'huîtres fossiles | os bancos de ostras fósseis
fossil beach | les plages fossiles | as praias fósseis
foraminiferal ooze | les boues à foraminifères | as vasas de foraminíferos
==
variability | la variabilité | a variabilidade
reconstruction | la reconstruction | a reconstrução
~ proxy records | les enregistrements indirects | os registros indiretos
trends | les tendances | as tendências
~ modelling | la modélisation | a modelagem
--
past storminess | la tempétuosité passée | a tempestuosidade pretérita
bottom water temperature | la température des eaux de fond | a temperatura da água de fundo
sea ice extent | l'extension de la glace de mer | a extensão do gelo marinho
upwelling intensity | l'intensité de l'upwelling | a intensidade da ressurgência
deep-water circulation | la circulation profonde | a circulação profunda
dust deposition | les dépôts de poussières | a deposição de poeira
monsoon intensity | l'intensité de la mousson | a intensidade das monções
ocean oxygenation | l'oxygénation océanique | a oxigenação oceânica
sediment flux | les flux sédimentaires | os fluxos sedimentares
surface salinity | la salinité de surface | a salinidade superficial
ice sheet extent | l'extension des calottes | a extensão dos mantos de gelo
glacial runoff | les apports glaciaires | os aportes glaciais
El Niño activity | l'activité d'El Niño | a atividade do El Niño
==
transfer functions | les fonctions de transfert | as funções de transferência
preservation | la préservation | a preservação
abundance changes | les variations d'abondance | as variações de abundância
stable isotopes | les isotopes stables | os isótopos estáveis
~ counting | le comptage | a contagem
--
ostracod | les ostracodes | os ostracodes
diatom frustule | les frustules de diatomées | as frústulas de diatomáceas
testate amoeba | les thécamoebiens | as tecamebas
pollen grain | les grains de pollen | os grãos de pólen
chironomid | les chironomes | os quironomídeos
radiolarian test | les tests de radiolaires | as carapaças de radiolários
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

    A compound that repeats a base term of any theme, in English or French, is
    left out, so every English form keeps a single French form. The
    Portuguese compound is ``<aspect> <de + object>`` (``a biomassa da
    merluza``).
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
                both_pt = bool(aspect.pt and obj.pt)
                term = Term(
                    en=f"{obj.en} {aspect.en}",
                    fr=f"{aspect.fr} {obj.fr_de}",
                    article=aspect.article,
                    technique=aspect.technique,
                    focus=obj.en,
                    pt=f"{aspect.pt} {obj.pt_de}" if both_pt else "",
                    pt_article=aspect.pt_article if both_pt else "",
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
        name_pt="Geomorfologia costeira e transporte de sedimentos",
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
        name_pt="Circulação oceânica e marés",
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
        name_pt="Ecologia marinha e biodiversidade",
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
        name_pt="Pesca e aquicultura",
        kind="natural",
        neighbours=("marine-ecology", "coastal-governance", "blue-economy"),
        group_stems=("Fisheries Science", "Aquaculture Research", "Living Resources"),
        terms=parse_terms(_FISHERIES) + compose_terms(_FISHERIES_FAMILIES),
    ),
    Theme(
        id="plankton-biogeochemistry",
        name_en="Plankton and biogeochemistry",
        name_fr="Plancton et biogéochimie",
        name_pt="Plâncton e biogeoquímica",
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
        name_pt="Riscos costeiros e elevação do nível do mar",
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
        name_pt="Observação do oceano e sensoriamento remoto",
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
        name_pt="Poluição marinha e microplásticos",
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
        name_pt="Sociedades costeiras e governança",
        kind="social",
        neighbours=("coastal-hazards", "fisheries-aquaculture", "blue-economy"),
        group_stems=("Coastal Societies", "Maritime Governance", "Littoral Territories"),
        terms=parse_terms(_GOVERNANCE) + compose_terms(_GOVERNANCE_FAMILIES),
    ),
    Theme(
        id="blue-economy",
        name_en="Ports, shipping and the blue economy",
        name_fr="Ports, transport maritime et économie bleue",
        name_pt="Portos, transporte marítimo e economia azul",
        kind="social",
        neighbours=("coastal-governance", "marine-pollution", "fisheries-aquaculture"),
        group_stems=("Maritime Economics", "Ports and Shipping", "Blue Economy"),
        terms=parse_terms(_BLUE_ECONOMY) + compose_terms(_BLUE_ECONOMY_FAMILIES),
    ),
    Theme(
        id="estuaries-wetlands",
        name_en="Estuaries and wetlands",
        name_fr="Estuaires et zones humides",
        name_pt="Estuários e áreas úmidas",
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
        name_pt="Paleoceanografia e arquivos climáticos",
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
numerical modelling | la modélisation numérique | a modelagem numérica
field surveys | les campagnes de terrain | as campanhas de campo
in situ measurements | les mesures in situ | as medições in situ
flume experiments | les expériences en canal hydraulique | os experimentos em canal hidráulico
time series analysis | l'analyse de séries temporelles | a análise de séries temporais
generalised additive models | les modèles additifs généralisés | os modelos aditivos generalizados
mixed-effects models | les modèles à effets mixtes | os modelos de efeitos mistos
principal component analysis | l'analyse en composantes principales | a análise de componentes principais
spatial statistics | la statistique spatiale | a estatística espacial
stable isotope analysis | l'analyse des isotopes stables | a análise de isótopos estáveis
mass balance approaches | les approches par bilan de masse | as abordagens de balanço de massa
ensemble simulations | les simulations d'ensemble | as simulações por conjunto
long-term monitoring | le suivi à long terme | o monitoramento de longo prazo
mesocosm experiments | les expériences en mésocosmes | os experimentos em mesocosmos
high-throughput sequencing | le séquençage à haut débit | o sequenciamento de alto desempenho
geochemical analyses | les analyses géochimiques | as análises geoquímicas
acoustic surveys | les relevés acoustiques | os levantamentos acústicos
spectral analysis | l'analyse spectrale | a análise espectral
wavelet analysis | l'analyse en ondelettes | a análise de ondaletas
high-resolution simulations | les simulations à haute résolution | as simulações de alta resolução
coupled ocean-wave models | les modèles couplés océan-vagues | os modelos acoplados oceano-ondas
trend analysis | l'analyse de tendances | a análise de tendências
hindcast simulations | les simulations rétrospectives | as simulações retrospectivas
field sampling | l'échantillonnage de terrain | a amostragem de campo
deep learning | l'apprentissage profond | o aprendizado profundo
sensitivity analysis | l'analyse de sensibilité | a análise de sensibilidade
uncertainty quantification | la quantification des incertitudes | a quantificação de incertezas
remote sensing | la télédétection | o sensoriamento remoto
"""

_METHODS_SOCIAL = """
comparative case analysis | l'analyse comparative de cas | a análise comparativa de casos
documentary analysis | l'analyse documentaire | a análise documental
archival research | le travail d'archives | a pesquisa em arquivos
participant observation | l'observation participante | a observação participante
mixed methods | les méthodes mixtes | os métodos mistos
econometric models | les modèles économétriques | os modelos econométricos
participatory mapping | la cartographie participative | o mapeamento participativo
semi-structured interviews | les entretiens semi-directifs | as entrevistas semiestruturadas
focus groups | les groupes de discussion | os grupos focais
ethnographic fieldwork | le terrain ethnographique | o trabalho de campo etnográfico
institutional analysis | l'analyse institutionnelle | a análise institucional
public policy analysis | l'analyse des politiques publiques | a análise de políticas públicas
discourse analysis | l'analyse de discours | a análise do discurso
questionnaire surveys | les enquêtes par questionnaire | os levantamentos por questionário
household surveys | les enquêtes auprès des ménages | as pesquisas domiciliares
social network analysis | l'analyse des réseaux sociaux | a análise de redes sociais
qualitative content analysis | l'analyse de contenu qualitative | a análise de conteúdo qualitativa
"""

_METHODS_ANY = """
machine learning | l'apprentissage automatique | o aprendizado de máquina
Bayesian inference | l'inférence bayésienne | a inferência bayesiana
scenario analysis | l'analyse de scénarios | a análise de cenários
meta-analysis | la méta-analyse | a meta-análise
geographic information systems | les systèmes d'information géographique | os sistemas de informação geográfica
network analysis | l'analyse de réseaux | a análise de redes
agent-based modelling | la modélisation multi-agents | a modelagem baseada em agentes
cluster analysis | la classification hiérarchique | a análise de agrupamentos
"""

METHODS: tuple[Method, ...] = (
    tuple(Method(t, "natural") for t in parse_terms(_METHODS_NATURAL))
    + tuple(Method(t, "social") for t in parse_terms(_METHODS_SOCIAL))
    + tuple(Method(t, "any") for t in parse_terms(_METHODS_ANY))
)

# Locative phrases: where a study takes place.
SETTINGS: tuple[Setting, ...] = (
    Setting("on sandy beaches", "sur les plages sableuses", social=True, pt="nas praias arenosas"),
    Setting("on rocky coasts", "sur les côtes rocheuses", social=True, pt="nos costões rochosos"),
    Setting(
        "in semi-enclosed bays",
        "dans les baies semi-fermées",
        social=True,
        pt="nas baías semifechadas",
    ),
    Setting(
        "on the continental shelf", "sur le plateau continental", pt="na plataforma continental"
    ),
    Setting(
        "in shallow coastal waters",
        "dans les eaux côtières peu profondes",
        pt="nas águas costeiras rasas",
    ),
    Setting(
        "in temperate coastal seas",
        "dans les mers côtières tempérées",
        pt="nos mares costeiros temperados",
    ),
    Setting(
        "on tropical coastlines",
        "sur les littoraux tropicaux",
        social=True,
        pt="nos litorais tropicais",
    ),
    Setting(
        "in high-latitude fjords",
        "dans les fjords de haute latitude",
        pt="nos fiordes de altas latitudes",
    ),
    Setting(
        "on urbanised coastlines",
        "sur les littoraux urbanisés",
        social=True,
        pt="nos litorais urbanizados",
    ),
    Setting(
        "on tide-dominated coasts",
        "sur les côtes dominées par la marée",
        pt="nas costas dominadas por marés",
    ),
    Setting(
        "on wave-dominated coasts",
        "sur les côtes dominées par la houle",
        pt="nas costas dominadas por ondas",
    ),
    Setting(
        "in microtidal environments",
        "dans les environnements microtidaux",
        pt="nos ambientes de micromaré",
    ),
    Setting(
        "in deltaic systems",
        "dans les systèmes deltaïques",
        social=True,
        pt="nos sistemas deltaicos",
    ),
    Setting(
        "on barrier coasts",
        "sur les côtes à cordons littoraux",
        social=True,
        pt="nas costas com barreiras",
    ),
    Setting("in the open ocean", "au large", pt="em mar aberto"),
    Setting("in enclosed seas", "dans les mers fermées", pt="nos mares fechados"),
    Setting(
        "in eastern boundary upwelling regions",
        "dans les régions d'upwelling de bord est",
        pt="nas regiões de ressurgência de borda leste",
    ),
    Setting("on small islands", "dans les petites îles", social=True, pt="nas pequenas ilhas"),
    Setting("in the intertidal zone", "dans la zone intertidale", pt="na zona entremarés"),
    Setting("in the coastal zone", "dans la zone côtière", social=True, pt="na zona costeira"),
)

DRIVERS: tuple[Term, ...] = parse_terms(
    """
climate change | le changement climatique | as mudanças climáticas
anthropogenic pressures | les pressions anthropiques | as pressões antrópicas
extreme events | les événements extrêmes | os eventos extremos
interannual variability | la variabilité interannuelle | a variabilidade interanual
seasonal forcing | le forçage saisonnier | o forçamento sazonal
long-term warming | le réchauffement à long terme | o aquecimento de longo prazo
land-use change | les changements d'usage des sols | as mudanças no uso da terra
coastal development | l'aménagement du littoral | a ocupação do litoral
storm events | les épisodes de tempête | os episódios de tempestade
river inputs | les apports fluviaux | os aportes fluviais
"""
)


def all_terms() -> tuple[Term, ...]:
    """Every distinct theme term (bridge terms once), in theme order."""
    seen: dict[str, Term] = {}
    for theme in THEMES:
        for term in theme.terms:
            seen.setdefault(term.en, term)
    return tuple(seen.values())
