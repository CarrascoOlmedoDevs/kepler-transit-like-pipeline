"""Telescopios soportados. Kepler es la referencia: catálogo KOI/TCE y modelos RF/CNN
se entrenaron con Kepler DR25. K2 y TESS reutilizan BLS, vetting y figuras, pero sus
probabilidades RF/CNN no están calibradas para esos instrumentos."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Mission:
    key: str
    prefix: str            # Identificador estelar: KIC, EPIC, TIC
    lk_mission: str        # Nombre de misión en Lightkurve/MAST
    segment: str           # quarter / campaign / sector
    searches: tuple        # Productos a probar en orden: (author, opciones extra)
    calibrated: bool       # RF/CNN y catálogo KOI válidos
    id_columns: tuple      # Columnas aceptadas en CSV de scan-list

    def label(self, target):
        return f'{self.prefix} {int(target)}'

    def folder(self, target):
        return f'{self.prefix}_{int(target)}'


MISSIONS = {
    'kepler': Mission('kepler', 'KIC', 'Kepler', 'quarter',
                      (('Kepler', {'cadence': 'long'}),), True, ('kepid', 'kic')),
    'k2': Mission('k2', 'EPIC', 'K2', 'campaign',
                  (('K2', {'cadence': 'long'}),), False, ('epic', 'epic_id', 'epicid', 'target_id')),
    # SPOC 2 min primero; si la estrella no tiene, curvas TESS-SPOC de imágenes completas (FFI).
    'tess': Mission('tess', 'TIC', 'TESS', 'sector',
                    (('SPOC', {'exptime': 120}), ('TESS-SPOC', {})), False,
                    ('tic', 'tic_id', 'ticid', 'target_id')),
}


def get(name):
    try:
        return MISSIONS[str(name).lower()]
    except KeyError:
        raise ValueError(f'Misión desconocida: {name}; use una de {", ".join(MISSIONS)}') from None
