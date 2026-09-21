"""Configuración serializable: las unidades de tiempo son días BKJD (TDB)."""
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Config:
    period_min: float = 0.5
    period_max: float = 300.0
    durations: tuple = (0.05, 0.1, 0.2, 0.3, 0.45)
    flatten_window: float = 5.0
    sigma_clip: float = 7.0
    coarse_points: int = 30000
    refine_peaks: int = 12
    max_planets: int = 5
    min_snr: float = 7.1
    min_transits: int = 2
    match_tolerance: float = 0.01
    seed: int = 42
    quarter: int | None = None
    save_curves: bool = False

    def __post_init__(self):
        if not 0 < self.period_min < self.period_max:
            raise ValueError('Se requiere 0 < period_min < period_max')
        if not self.durations or min(self.durations) <= 0:
            raise ValueError('Duraciones positivas requeridas')
        if max(self.durations) >= self.period_min:
            raise ValueError('Cada duración debe ser menor que period_min')
        if self.flatten_window < 5 * max(self.durations):
            raise ValueError('flatten_window debe ser >= 5 veces la duración máxima')
        if self.sigma_clip <= 0 or self.coarse_points < 100:
            raise ValueError('sigma_clip > 0 y coarse_points >= 100 requeridos')
        if min(self.refine_peaks, self.max_planets, self.min_transits) < 1:
            raise ValueError('Contadores deben ser positivos')
        if self.min_snr <= 0 or not 0 < self.match_tolerance < 0.5:
            raise ValueError('SNR/tolerancia inválidos')

    def to_dict(self):
        return asdict(self)
