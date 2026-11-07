"""Vocabulaire fermé des caractéristiques visuelles détectables sur les photos d'annonces.

Chaque caractéristique a :
- une définition (envoyée à Claude pour l'analyse des photos) ;
- des termes de requête (« façade en pierre », « pierres »…) pour comprendre la recherche ;
- des termes d'annonce, plus stricts, pour repérer la caractéristique dans le texte de l'annonce.
"""
from __future__ import annotations

from dataclasses import dataclass

GROUPS: dict[str, str] = {
    "facade": "Façade & architecture",
    "terrain": "Terrain & extérieurs",
    "paysage": "Paysage & environnement",
    "interieur": "Intérieur",
    "etat": "État du bien",
}


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    group: str
    definition: str
    query_terms: tuple[str, ...]
    text_terms: tuple[str, ...] = ()
    undesirable: bool = False


FEATURES: tuple[Feature, ...] = (
    # --- Façade & architecture -------------------------------------------------
    Feature("facade_pierre", "Façade en pierre", "facade",
            "Murs extérieurs en pierre apparente (moellons, granit, calcaire, pierre de taille, schiste), "
            "y compris partiellement. Un simple soubassement ou un muret ne suffit pas.",
            ("facade en pierre", "facade pierre", "maison en pierre", "en pierre", "en pierres", "pierre",
             "pierres", "pierre de taille", "moellons", "granit", "longere", "mas en pierre"),
            ("maison en pierre", "maison en pierres", "batisse en pierre", "en pierres apparentes",
             "facade en pierre", "pierre de taille", "moellons", "longere en pierre", "murs en pierre",
             "en pierre du pays", "en granit", "corps de ferme en pierre")),
    Feature("facade_brique", "Façade en brique", "facade",
            "Murs extérieurs en brique apparente.",
            ("brique", "briques", "en brique", "facade brique"),
            ("en brique", "en briques", "briques apparentes", "facade brique")),
    Feature("facade_bois", "Façade / bardage bois", "facade",
            "Bardage ou murs extérieurs en bois (chalet, ossature bois visible, clins).",
            ("bardage bois", "facade bois", "maison bois", "ossature bois", "chalet", "maison en bois"),
            ("bardage bois", "maison bois", "maison en bois", "ossature bois", "chalet en bois")),
    Feature("facade_enduit", "Façade enduite", "facade",
            "Façade recouverte d'un enduit ou crépi (aucune pierre ni brique apparente).",
            ("enduit", "crepi", "facade enduite"),
            ("facade enduite", "ravalement")),
    Feature("colombages", "Colombages", "facade",
            "Façade à pans de bois / colombages visibles (style normand, alsacien…).",
            ("colombages", "colombage", "pans de bois", "maison normande"),
            ("colombages", "colombage", "pans de bois")),
    Feature("toit_ardoise", "Toiture en ardoise", "facade",
            "Couverture du toit en ardoise (gris-bleu foncé, plaques fines).",
            ("ardoise", "ardoises", "toit en ardoise", "toiture ardoise"),
            ("toiture ardoise", "toit en ardoise", "couverture ardoise", "en ardoises")),
    Feature("toit_tuiles", "Toiture en tuiles", "facade",
            "Couverture du toit en tuiles (canal, romanes, plates, mécaniques).",
            ("tuiles", "toit en tuiles", "tuiles canal"),
            ("toiture tuiles", "toit en tuiles", "tuiles canal")),
    Feature("style_ancien", "Bâti ancien de caractère", "facade",
            "Construction ancienne de caractère (avant ~1950) : longère, mas, maison de maître, ferme, "
            "manoir, maison de bourg ancienne.",
            ("ancien", "ancienne", "de caractere", "authentique", "charme de l ancien", "maison de maitre",
             "longere", "mas", "corps de ferme", "fermette", "manoir", "bastide"),
            ("maison de caractere", "maison de maitre", "longere", "corps de ferme", "fermette", "manoir",
             "bastide", "mas provencal", "charme de l ancien", "maison ancienne")),
    Feature("style_contemporain", "Architecture contemporaine", "facade",
            "Architecture contemporaine : volumes cubiques, toit plat, grandes baies, matériaux modernes.",
            ("contemporaine", "moderne", "architecte", "maison d architecte", "toit plat"),
            ("contemporaine", "maison d architecte", "toit plat", "architecture moderne")),

    # --- Terrain & extérieurs ----------------------------------------------------
    Feature("terrain_plat", "Terrain plat", "terrain",
            "Terrain visiblement plat ou quasi plat autour de la maison.",
            ("terrain plat", "jardin plat", "plat", "plain-pied et plat"),
            ("terrain plat", "jardin plat", "parfaitement plat")),
    Feature("terrain_pente", "Terrain en pente", "terrain",
            "Terrain en pente marquée, en restanques ou en terrasses.",
            ("en pente", "pente", "pentu", "restanques", "terrain en terrasses"),
            ("terrain en pente", "en restanques", "terrain pentu", "en pente douce", "en terrasses")),
    Feature("terrain_arbore", "Terrain arboré", "terrain",
            "Terrain avec plusieurs arbres (feuillus, conifères, fruitiers…).",
            ("arbore", "arbores", "arbres", "terrain arbore", "jardin arbore", "boise", "ombrage"),
            ("arbore", "arboree", "arbres centenaires", "arbres fruitiers", "terrain boise", "parc arbore")),
    Feature("pelouse", "Pelouse / jardin d'agrément", "terrain",
            "Pelouse ou jardin d'agrément entretenu.",
            ("pelouse", "gazon", "jardin d agrement", "jardin"),
            ("pelouse", "jardin d agrement", "gazon")),
    Feature("potager_verger", "Potager / verger", "terrain",
            "Potager, carrés de culture ou verger visibles.",
            ("potager", "verger", "fruitiers", "arbres fruitiers"),
            ("potager", "verger")),
    Feature("haies_intimite", "Jardin intime (haies, murs)", "terrain",
            "Jardin protégé des regards par des haies hautes, des murs ou de la végétation : pas de vis-à-vis.",
            ("sans vis a vis", "pas de vis a vis", "intimite", "haies", "clos de murs", "jardin clos", "au calme"),
            ("sans vis a vis", "aucun vis a vis", "clos de murs", "jardin clos", "a l abri des regards")),
    Feature("piscine", "Piscine", "terrain",
            "Piscine (enterrée ou hors-sol) visible.",
            ("piscine",),
            ("piscine",)),
    Feature("terrasse", "Terrasse", "terrain",
            "Terrasse extérieure aménagée (bois, dallage, pierre).",
            ("terrasse", "terrasses"),
            ("terrasse",)),
    Feature("eau_sur_terrain", "Point d'eau (étang, rivière)", "terrain",
            "Étang, mare, ruisseau ou rivière sur le terrain ou en limite immédiate.",
            ("etang", "riviere", "ruisseau", "mare", "bord de l eau", "cours d eau", "bord de riviere"),
            ("etang", "ruisseau", "bord de riviere", "riviere en bordure", "mare", "cours d eau")),
    Feature("grand_terrain", "Grand terrain / espace", "terrain",
            "Terrain visiblement vaste (prairie, parc, plusieurs milliers de m²).",
            ("grand terrain", "vaste terrain", "beaucoup de terrain", "grand jardin", "parc", "hectare",
             "hectares"),
            ("grand terrain", "vaste terrain", "parc de", "hectare", "hectares")),

    # --- Paysage & environnement -------------------------------------------------
    Feature("vue_degagee", "Vue dégagée", "paysage",
            "Vue lointaine et dégagée depuis la maison ou le terrain (horizon visible, pas d'obstacle proche).",
            ("vue degagee", "vue imprenable", "vue panoramique", "panorama", "belle vue", "vue lointaine"),
            ("vue degagee", "vue imprenable", "vue panoramique", "vue exceptionnelle")),
    Feature("vue_mer", "Vue mer", "paysage",
            "La mer ou l'océan est visible depuis le bien.",
            ("vue mer", "vue sur la mer", "vue ocean", "vue sur l ocean", "face a la mer"),
            ("vue mer", "vue sur la mer", "vue ocean", "vue sur l ocean", "face a la mer")),
    Feature("vue_montagne", "Vue montagne", "paysage",
            "Montagnes ou reliefs marqués visibles depuis le bien.",
            ("vue montagne", "vue sur les montagnes", "vue sur la montagne", "montagne", "vue alpes",
             "vue sur les alpes", "vue pyrenees"),
            ("vue montagne", "vue sur les montagnes", "vue sur la montagne", "vue sur les alpes",
             "vue sur les pyrenees")),
    Feature("campagne", "Cadre campagne", "paysage",
            "Environnement rural : champs, prairies, bocage, pas de voisinage dense.",
            ("campagne", "en campagne", "champs", "bocage", "prairie", "rural", "nature"),
            ("en pleine campagne", "en campagne", "cadre champetre", "bocage", "au milieu des champs")),
    Feature("foret", "Forêt / bois", "paysage",
            "Forêt ou bois en bordure ou visible à proximité immédiate.",
            ("foret", "bois", "lisiere", "en lisiere de foret", "proche foret"),
            ("en lisiere de foret", "lisiere de bois", "foret domaniale", "bordure de foret", "entoure de bois")),
    Feature("vignes", "Vignes", "paysage",
            "Vignes ou vignoble visibles autour du bien.",
            ("vignes", "vignoble", "vigne"),
            ("vignes", "vignoble")),
    Feature("urbain", "Environnement urbain", "paysage",
            "Environnement de ville : rue, immeubles, maisons mitoyennes.",
            ("centre ville", "en ville", "urbain", "centre-ville"),
            ("centre ville", "hyper centre", "coeur de ville")),
    Feature("vis_a_vis", "Vis-à-vis visible", "paysage",
            "Voisins proches visibles depuis le jardin ou les fenêtres (vis-à-vis).",
            ("vis a vis",),
            ("vis a vis",), undesirable=True),

    # --- Intérieur ---------------------------------------------------------------
    Feature("poutres", "Poutres apparentes", "interieur",
            "Poutres ou charpente apparentes à l'intérieur.",
            ("poutres", "poutres apparentes", "charpente apparente"),
            ("poutres apparentes", "charpente apparente", "poutres")),
    Feature("cheminee", "Cheminée / poêle", "interieur",
            "Cheminée, insert ou poêle à bois.",
            ("cheminee", "poele", "insert", "poele a bois"),
            ("cheminee", "poele a bois", "insert", "poele a granules")),
    Feature("pierre_interieur", "Pierre apparente intérieure", "interieur",
            "Murs intérieurs en pierre apparente.",
            ("pierres apparentes", "mur en pierre", "murs en pierre interieur"),
            ("pierres apparentes", "murs en pierres apparentes")),
    Feature("parquet", "Parquet", "interieur",
            "Parquet bois au sol.",
            ("parquet",),
            ("parquet",)),
    Feature("lumineux", "Intérieur lumineux", "interieur",
            "Pièces très lumineuses, grandes ouvertures, baies vitrées.",
            ("lumineux", "lumineuse", "baies vitrees", "baie vitree", "luminosite"),
            ("tres lumineux", "tres lumineuse", "baies vitrees", "baie vitree", "baigne de lumiere")),

    # --- État ----------------------------------------------------------------------
    Feature("a_renover", "À rénover", "etat",
            "Travaux importants visibles : façade abîmée, intérieur vétuste, chantier.",
            ("a renover", "travaux", "a restaurer", "a rehabiliter", "a rafraichir", "renovation"),
            ("a renover", "travaux a prevoir", "a restaurer", "a rehabiliter", "renovation complete")),
    Feature("renove", "Rénové / bon état", "etat",
            "Bien récemment rénové ou en très bon état, prêt à habiter.",
            ("renove", "renovee", "refait a neuf", "bon etat", "sans travaux", "pret a habiter"),
            ("entierement renove", "entierement renovee", "refait a neuf", "aucun travaux", "sans travaux",
             "excellent etat", "renovation recente")),
)

FEATURES_BY_KEY: dict[str, Feature] = {f.key: f for f in FEATURES}
FEATURE_KEYS: tuple[str, ...] = tuple(FEATURES_BY_KEY)

# Types de photo retournés par l'analyse.
IMAGE_KINDS: tuple[str, ...] = ("facade", "jardin_terrain", "vue_paysage", "interieur", "plan_carte", "autre")


def taxonomy_prompt() -> str:
    """Description de la taxonomie pour les prompts Claude."""
    lines = []
    for group, title in GROUPS.items():
        lines.append(f"## {title}")
        for f in FEATURES:
            if f.group == group:
                lines.append(f"- {f.key} — {f.label} : {f.definition}")
        lines.append("")
    return "\n".join(lines).strip()
