"""Довідники гідрологічних постів і метеостанцій HydroBulletin."""

from __future__ import annotations

from .models import Station


LVIV_STATIONS: tuple[Station, ...] = (
    Station("81015", "Дністер — Стрілки"),
    Station("81017", "Дністер — Самбір"),
    Station("81028", "Дністер — Розділ"),
    Station("81030", "Дністер — Журавно"),
    Station("81078", "Стрв’яж — Хирів"),
    Station("81080", "Стрв’яж — Луки"),
    Station("81085", "Верещиця — Комарно"),
    Station("81087", "Бистриця — Озимина"),
    Station("81092", "Тисмениця — Дрогобич"),
    Station("81102", "Стрий — Матків"),
    Station("81103", "Стрий — Завадівка"),
    Station("81468", "Стрий — Ясениця"),
    Station("81108", "Стрий — Верхнє Синьовидне"),
    Station("81109", "Стрий — Стрий"),
    Station("81113", "Яблунька — Турка"),
    Station("81465", "Рибник — Майдан"),
    Station("81469", "Завадка — Риків"),
    Station("81120", "Опір — Сколе"),
    Station("81122", "Славська — Славсько"),
    Station("81126", "Головчанка — Тухля"),
    Station("81129", "Орява — Святослав"),
    Station("81147", "Свіча — Зарічне"),
    Station("79720", "Вишня — Твіржа"),
    Station("79723", "Західний Буг — Сасів"),
    Station("79726", "Західний Буг — Кам’янка-Бузька"),
    Station("79747", "Полтва — Буськ"),
    Station("79753", "Рата — Волиця"),
    Station("79755", "Рата — Межиріччя"),
    Station("79757", "Свиня — Жовква"),
    Station("79761", "Солокія — Шептицький"),
    Station("79473", "Стир — Щуровичі"),
)

IF_STATIONS: tuple[Station, ...] = (
    Station("81033", "Дністер — Галич"),
    Station("81036", "Дністер — Нижнів"),
    Station("81041", "Дністер — Заліщики"),
    Station("81140", "Свіча — Мислівка"),
    Station("81151", "Лужанка — Гошів"),
    Station("81152", "Сукель — Тисів"),
    Station("81156", "Свір — Букачівці"),
    Station("81161", "Лімниця — Осмолода"),
    Station("81169", "Лімниця — Перевозець"),
    Station("81172", "Чечва — Спас"),
    Station("81178", "Луква — Боднарів"),
    Station("81184", "Гнила Липа — Більшівці"),
    Station("81191", "Бистриця Надвірнянська — Пасічна"),
    Station("81471", "Бистриця Надвірнянська — Черніїв"),
    Station("81197", "Ворона — Тисмениця"),
    Station("81199", "Бистриця Солотвинська — Гута"),
    Station("81203", "Бистриця Солотвинська — Івано-Франківськ"),
)

LEFT_DNISTER_STATIONS: tuple[Station, ...] = (
    Station("81041", "Дністер — Заліщики"),
    Station("81205", "Золота Липа — Бережани"),
    Station("81206", "Золота Липа — Задарів"),
    Station("81209", "Коропець — Підгайці"),
    Station("81210", "Коропець — Коропець"),
    Station("81213", "Стрипа — Каплинці"),
    Station("81215", "Стрипа — Бучач"),
    Station("81219", "Серет — Велика Березовиця"),
    Station("81225", "Серет — Чортків"),
    Station("81230", "Нічлава — Стрілківці"),
    Station("81232", "Збруч — Волочиськ"),
    Station("81236", "Збруч — Завалля"),
    Station("81241", "Жванчик — Кугаївці"),
    Station("81242", "Жванчик — Ластівці"),
    Station("81243", "Смотрич — Купин"),
    Station("81244", "Смотрич — Цибулівка"),
    Station("81245", "Мукша — Мала Слобідка"),
    Station("81249", "Студениця — Голозубинці"),
    Station("81250", "Ушиця — Зіньків"),
    Station("81251", "Ушиця — Тимків"),
    Station("81254", "Калюс — Нова Ушиця"),
    Station("81257", "Лядова — Жеребилівка"),
    Station("81261", "Мурафа — Кудіївці"),
    Station("81267", "Марківка — Підлісівка"),
)

METEO_STATIONS: tuple[Station, ...] = (
    Station("33288", "Метеостанція Кам’янка-Бузька"),
    Station("33398", "Метеостанція Дрогобич"),
    Station("33409", "Метеостанція Бережани"),
    Station("33511", "Метеостанція Турка"),
    Station("33513", "Метеостанція Стрий"),
    Station("33516", "Метеостанція Славське"),
    Station("33526", "Метеостанція Івано-Франківськ"),
    Station("33536", "Метеостанція Чортків"),
    Station("33548", "Метеостанція Кам’янець-Подільський"),
    Station("33557", "Метеостанція Нова Ушиця"),
)


def _unique_stations(*groups: tuple[Station, ...]) -> tuple[Station, ...]:
    result: list[Station] = []
    seen: set[str] = set()
    for group in groups:
        for station in group:
            if station.index in seen:
                continue
            seen.add(station.index)
            result.append(station)
    return tuple(result)


HYDRO_STATIONS: tuple[Station, ...] = _unique_stations(
    LVIV_STATIONS,
    IF_STATIONS,
    LEFT_DNISTER_STATIONS,
)
ALL_STATIONS: tuple[Station, ...] = _unique_stations(
    HYDRO_STATIONS,
    METEO_STATIONS,
)

LVIV_STATIONS_BY_INDEX: dict[str, Station] = {
    station.index: station for station in LVIV_STATIONS
}
STATIONS_BY_INDEX: dict[str, Station] = {
    station.index: station for station in HYDRO_STATIONS
}
ALL_STATIONS_BY_INDEX: dict[str, Station] = {
    station.index: station for station in ALL_STATIONS
}
