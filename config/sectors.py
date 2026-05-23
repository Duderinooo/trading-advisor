"""Sector mapping + cluster-risk + correlation gate."""

# Sector-Correlation-Gate: max Positionen pro Sektor verhindert Klumpenrisiko
# (z.B. 3× Semis gleichzeitig → Chip-Crash = 3 Stops auf einmal)
MAX_POSITIONS_PER_SECTOR = 2

SECTOR_MAP = {
    # Semiconductors
    "NVD.DE": "semis",       # legacy: open positions only (gefiltert für neue Entries)
    "AMD.DE": "semis",       # legacy
    "INL.DE": "semis",
    "IFX.DE": "semis",
    # Semi-Equipment / AI-Infra (added 2026-05-20 — eigenes Cluster, damit nicht
    # alle Halbleiter-Namen in einer Sektor-Cap-Linie liegen)
    "AIXA.DE": "semi_equipment",   # Aixtron
    "WAF.DE": "semi_equipment",    # Siltronic
    "SMHN.DE": "semi_equipment",   # Suss MicroTec
    # Enterprise Software / Cloud
    "MSF.DE": "software_cloud",  # legacy
    "SAP.DE": "software_cloud",  # legacy
    # Consumer Tech
    "APC.DE": "consumer_tech",   # legacy
    # Fintech
    "2PP.DE": "fintech",
    # Internet/Mobility
    "UT8.DE": "internet",
    # E-commerce
    "ZAL.DE": "ecommerce",
    # EV/Auto
    "TL0.DE": "auto_ev",         # legacy
    # Industrial
    "SIE.DE": "industrial",      # legacy
    # Finance / Insurance
    "ALV.DE": "insurance",       # legacy
    # Banking
    "DBK.DE": "banking",
    "CBK.DE": "banking",
    # Telecom
    "DTE.DE": "telecom",
    # Logistics
    "DHL.DE": "logistics",
    # Pharma / Healthcare
    "BAYN.DE": "pharma",
    "FRE.DE": "healthcare",
    # Chemicals
    "BAS.DE": "chemicals",
    # Auto (ICE + Massenmarkt)
    "BMW.DE": "auto_ice",
    "MBG.DE": "auto_ice",
    "VOW3.DE": "auto_ice",
    "P911.DE": "auto_luxury",
    "CON.DE": "auto_supplier",
    # Consumer Goods
    "HEN3.DE": "consumer_goods",
    "PUM.DE": "consumer_goods",
    # Utility / Renewables
    "RWE.DE": "utility",
    "ENR.DE": "energy_transition",
    "NDX1.DE": "energy_transition",  # added 2026-05-20: Nordex (Wind)
    "S92.DE": "energy_transition",   # added 2026-05-20: SMA Solar
    "VH2.DE": "energy_transition",   # added 2026-05-20: Friedrich Vorwerk (Energie-Infra / H2)
    "VBK.DE": "energy_transition",   # added 2026-05-20: VERBIO (Biofuels)
    # Aerospace / Defense
    "AIR.DE": "aerospace",       # legacy
    # Building Materials (cyclical)
    "HEI.DE": "building_materials",  # legacy
    # Commodities (already risk-diversified by nature but track)
    "3OIL.MI": "oil",
    "3BRL.MI": "oil",   # Brent 3x — same sector cluster as WTI for diversification gate
    "4GLD.DE": "gold",
    "EXX1.DE": "silver",
    "U3O8.DE": "uranium",
    "NUKL.DE": "uranium",
    "EXXY.DE": "broad_commodity",
}

# Korrelations-Gate: blockt Cluster-Risk auch quer durch Sektoren.
# 60d daily-return correlation zu existing holdings ≥ MAX_CORRELATION → Block.
CORRELATION_LOOKBACK_DAYS = 60
MAX_CORRELATION = 0.7
MAX_CORRELATED_HOLDINGS = 1       # max 1 hochkorrelierte Position erlaubt; ab 2 → Block
