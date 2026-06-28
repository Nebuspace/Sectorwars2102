"""Planet-name corpus + compound affixes (ADR-0073).

A curated bank of >=500 base planet names plus prefix/suffix affixes. A
deterministic generator (planet_naming_service) draws a base name and, with
weighted probability, attaches a prefix and/or suffix to form compound
variations ("New Eden", "Zeta Kepler Prime"), so the effective name space is
tens of thousands while every base reads as a real, evocative world name.

Pure data — no imports, operator-editable. Names are drawn from mythology,
astronomy, exploration history, and invented sci-fi roots.
"""

# --- Prefixes (occasionally prepended) -------------------------------------
PLANET_PREFIXES: tuple = (
    "New", "Old", "Nova", "Neo", "Port", "Fort", "Far", "Lost", "Outer",
    "Inner", "Upper", "Lower", "Greater", "Lesser", "Alpha", "Beta", "Gamma",
    "Delta", "Zeta", "Sigma", "Omega", "Prime", "Free", "Saint", "Mount",
)

# --- Suffixes (occasionally appended) --------------------------------------
PLANET_SUFFIXES: tuple = (
    "Prime", "Major", "Minor", "Secundus", "Tertius", "Reach", "Rest",
    "Landing", "Station", "Colony", "Outpost", "Haven", "Hold", "Gate",
    "Expanse", "Drift", "Verge", "Crossing", "Cradle", "Watch", "Spire",
    "II", "III", "IV", "V", "IX", "Beta", "Prime", "Terminus", "Anchorage",
)

# --- Base names (>=500) -----------------------------------------------------
PLANET_BASE_NAMES: tuple = (
    # Greek / Roman mythology
    "Eden", "Elysium", "Olympus", "Arcadia", "Hyperion", "Helios", "Selene",
    "Atlas", "Prometheus", "Pandora", "Persephone", "Demeter", "Artemis",
    "Apollo", "Hermes", "Hestia", "Poseidon", "Triton", "Nereus", "Galatea",
    "Calypso", "Circe", "Ariadne", "Theseus", "Perseus", "Andromeda",
    "Cassiopeia", "Orion", "Achilles", "Hector", "Odysseus", "Penelope",
    "Icarus", "Daedalus", "Tantalus", "Sisyphus", "Aurora", "Vesta",
    "Ceres", "Juno", "Minerva", "Diana", "Vulcan", "Janus", "Fortuna",
    "Bellona", "Pomona", "Flora", "Luna", "Sol", "Terra", "Gaia", "Rhea",
    "Cronus", "Tethys", "Iapetus", "Phoebe", "Theia", "Leto", "Eos",
    "Nyx", "Erebus", "Tartarus", "Chaos", "Aether", "Hemera", "Thanatos",
    "Hypnos", "Nemesis", "Tyche", "Kratos", "Bia", "Nike", "Styx",
    # Norse mythology
    "Asgard", "Midgard", "Vanaheim", "Alfheim", "Jotunheim", "Niflheim",
    "Muspelheim", "Helheim", "Yggdrasil", "Valhalla", "Bifrost", "Odin",
    "Thor", "Freya", "Frigg", "Baldr", "Tyr", "Heimdall", "Loki", "Vidar",
    "Vali", "Forseti", "Bragi", "Idun", "Skadi", "Njord", "Ran", "Aegir",
    "Hodr", "Sif", "Magni", "Modi", "Fenrir", "Sleipnir", "Gungnir",
    "Mjolnir", "Gjallarhorn", "Ginnungagap", "Ragnarok", "Surtr", "Ymir",
    # Egyptian mythology
    "Anubis", "Osiris", "Isis", "Horus", "Ra", "Set", "Thoth", "Bastet",
    "Sekhmet", "Sobek", "Amun", "Aten", "Geb", "Nut", "Shu", "Tefnut",
    "Khnum", "Ptah", "Hathor", "Maat", "Nephthys", "Khonsu", "Montu",
    "Wadjet", "Nekhbet", "Serqet", "Heka", "Khepri", "Atum", "Nun",
    # Celtic / other mythology
    "Avalon", "Camelot", "Tir", "Annwn", "Brigid", "Lugh", "Danu", "Morrigan",
    "Cernunnos", "Belenus", "Nuada", "Ogma", "Manannan", "Epona", "Taranis",
    "Sucellus", "Arawn", "Rhiannon", "Gwydion", "Branwen", "Dagda",
    "Amaterasu", "Susanoo", "Tsukuyomi", "Izanagi", "Izanami", "Inari",
    "Raijin", "Fujin", "Hachiman", "Benten", "Quetzal", "Tezca", "Mictlan",
    "Xibalba", "Kukulkan", "Itzamna", "Viracocha", "Inti", "Pachamama",
    # Stars / constellations / astronomy
    "Vega", "Altair", "Deneb", "Rigel", "Betelgeuse", "Sirius", "Procyon",
    "Capella", "Arcturus", "Aldebaran", "Antares", "Spica", "Pollux",
    "Castor", "Regulus", "Bellatrix", "Mintaka", "Alnilam", "Alnitak",
    "Saiph", "Polaris", "Mizar", "Alcor", "Dubhe", "Merak", "Phecda",
    "Megrez", "Alioth", "Alkaid", "Thuban", "Kochab", "Hamal", "Sheratan",
    "Mirach", "Almach", "Algol", "Mirfak", "Atria", "Acrux", "Gacrux",
    "Mimosa", "Avior", "Miaplacidus", "Alphard", "Denebola", "Zosma",
    "Algieba", "Adhara", "Wezen", "Aludra", "Naos", "Suhail", "Markab",
    "Scheat", "Algenib", "Enif", "Sadalsuud", "Sadalmelik", "Fomalhaut",
    "Achernar", "Canopus", "Hadar", "Menkar", "Diphda", "Acamar", "Ankaa",
    "Kepler", "Gliese", "Trappist", "Proxima", "Centauri", "Tau", "Ceti",
    "Wolf", "Ross", "Lalande", "Luyten", "Barnard", "Kapteyn", "Teegarden",
    "Kruger", "Lacaille", "Groombridge", "Cordoba", "Bonner",
    "Lyra", "Cygnus", "Aquila", "Draco", "Hydra", "Pegasus", "Phoenix",
    "Corvus", "Crater", "Lupus", "Vela", "Carina", "Puppis", "Pyxis",
    "Norma", "Ara", "Pavo", "Grus", "Tucana", "Volans", "Dorado", "Mensa",
    "Reticulum", "Horologium", "Caelum", "Pictor", "Fornax", "Sculptor",
    "Antlia", "Sextans", "Leo", "Virgo", "Libra", "Scorpius", "Sagittarius",
    "Capricornus", "Aquarius", "Cetus", "Eridanus", "Lepus", "Columba",
    "Monoceros", "Lynx", "Auriga", "Perseus", "Cepheus", "Lacerta",
    "Vulpecula", "Sagitta", "Delphinus", "Equuleus", "Scutum", "Serpens",
    "Ophiuchus", "Corona", "Bootes", "Canes", "Coma", "Crux", "Musca",
    "Circinus", "Triangulum", "Apus", "Chamaeleon", "Octans", "Hydrus",
    # Exploration / pioneers / ships of history
    "Endeavour", "Discovery", "Resolution", "Endurance", "Beagle", "Victoria",
    "Santa", "Mayflower", "Pinta", "Nina", "Erebus", "Terror", "Fram",
    "Vostok", "Soyuz", "Apollo", "Gemini", "Mercury", "Mariner", "Viking",
    "Voyager", "Pioneer", "Galileo", "Cassini", "Magellan", "Pathfinder",
    "Sojourner", "Curiosity", "Perseverance", "Opportunity", "Spirit",
    "Genesis", "Stardust", "Juno", "Dawn", "Kepler", "Hubble", "Webb",
    "Newton", "Darwin", "Tesla", "Faraday", "Maxwell", "Curie", "Hawking",
    "Sagan", "Copernicus", "Brahe", "Halley", "Herschel", "Lowell",
    "Hubble", "Lemaitre", "Hertz", "Volta", "Ampere", "Ohm", "Kelvin",
    "Joule", "Pascal", "Bernoulli", "Euler", "Gauss", "Riemann", "Fermi",
    "Bohr", "Planck", "Heisenberg", "Dirac", "Feynman", "Pauli", "Schrodinger",
    # Invented sci-fi / evocative roots
    "Halcyon", "Solace", "Reverie", "Aurelia", "Lumina", "Caldera", "Cinder",
    "Ember", "Ashfall", "Frostmere", "Glacia", "Tundara", "Verdance",
    "Sylvan", "Thornwood", "Brightleaf", "Goldfield", "Amberglow", "Crimson",
    "Scarlet", "Vermillion", "Obsidian", "Onyx", "Basalt", "Marble", "Quartz",
    "Crystalis", "Prism", "Mirror", "Glass", "Vitrine", "Opaline", "Pearl",
    "Coral", "Marisol", "Tidewater", "Saltmarsh", "Brine", "Maelstrom",
    "Deepwell", "Abyssa", "Fathom", "Trench", "Currents", "Riptide",
    "Tempest", "Cyclone", "Monsoon", "Zephyr", "Mistral", "Sirocco",
    "Borealis", "Australis", "Meridian", "Equator", "Horizon", "Twilight",
    "Dawnstar", "Duskvale", "Nightfall", "Moonrise", "Starhaven", "Skyreach",
    "Cloudbreak", "Stormwatch", "Thunderhead", "Lightfall", "Sunward",
    "Westmark", "Eastmark", "Northwatch", "Southgate", "Highspire",
    "Deepforge", "Ironhold", "Steelheart", "Coppervein", "Silverlode",
    "Goldspire", "Platinar", "Cobalt", "Nickelite", "Titania", "Adamant",
    "Bastion", "Citadel", "Rampart", "Aegis", "Sentinel", "Vanguard",
    "Bulwark", "Redoubt", "Garrison", "Keep", "Spire", "Beacon", "Lantern",
    "Wayfarer", "Drifter", "Nomad", "Vagrant", "Pilgrim", "Wanderer",
    "Seeker", "Voyance", "Passage", "Threshold", "Frontier", "Outland",
    "Verdania", "Arborea", "Floralis", "Meadowlight", "Springfall", "Harvest",
    "Bountiful", "Plentywell", "Cornucopia", "Abundance", "Fertile",
    "Pastoral", "Homestead", "Hearthfire", "Kindred", "Concord", "Amity",
    "Harmony", "Serenity", "Tranquil", "Repose", "Sanctuary", "Refuge",
    "Asylum", "Respite", "Solitude", "Hermitage", "Cloister", "Reverence",
    "Providence", "Destiny", "Fortune", "Fate", "Chance", "Hazard", "Peril",
    "Venture", "Enterprise", "Ambition", "Aspire", "Zenith", "Apex",
    "Summit", "Pinnacle", "Crest", "Vertex", "Acme", "Crown", "Diadem",
    "Regalia", "Sovereign", "Imperial", "Dominion", "Empire", "Realm",
    "Kingdom", "Province", "County", "Shire", "Hamlet", "Township",
    "Borough", "Parish", "District", "Quarter", "Precinct", "Ward",
    "Vesper", "Matins", "Lauds", "Vigil", "Compline", "Angelus", "Evensong",
    "Carillon", "Chime", "Knell", "Toll", "Peal", "Resonance", "Echo",
    "Cadence", "Refrain", "Anthem", "Requiem", "Nocturne", "Aria", "Sonata",
    "Rhapsody", "Ballad", "Hymnal", "Psalter", "Canticle", "Madrigal",
    "Ignis", "Aqua", "Terra", "Ventus", "Lux", "Umbra", "Nox", "Dies",
    "Aurum", "Argent", "Ferrum", "Cuprum", "Stannum", "Plumbum", "Aurora",
    "Caelum", "Mare", "Mons", "Vallis", "Planitia", "Chasma", "Rupes",
    "Dorsa", "Fossa", "Labyrinthus", "Tholus", "Patera", "Lacus", "Sinus",
    "Palus", "Oceanus", "Cavus", "Colles", "Mensa", "Scopulus", "Sulcus",
    "Vastitas", "Terra", "Regio", "Macula", "Fluctus", "Linea", "Flexus",
)
