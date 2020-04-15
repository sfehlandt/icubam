import itertools
import json
import logging
#import os
import pickle
import urllib.request
from pathlib import Path
#from typing import Dict

import numpy as np
import pandas as pd
from lxml import html

BASE_PATH = Path(__file__).resolve().parent

DATA_SOURCES = {"icubam", "bedcounts", "public", "combined_bedcounts_public"}

DATA_PATHS = {
  "department_population": "data/department_population.csv",
  "departments": "data/france_departments.json",
  "department_typo_fixes": "data/icubam_department_typo_fixes.json",
}

for key, path in DATA_PATHS.items():
  DATA_PATHS[key] = Path(BASE_PATH) / path


def load_department_population():
  return dict(
    pd.read_csv(DATA_PATHS["department_population"]
                ).itertuples(name=None, index=False)
  )


def load_france_departments():
  return pd.read_json(DATA_PATHS["departments"])


CODE_TO_DEPARTMENT = dict(
  load_france_departments()[["departmentCode", "departmentName"]].itertuples(
    name=None,
    index=False,
  ),
)
DEPARTMENT_TO_CODE = dict(
  load_france_departments()[["departmentName", "departmentCode"]].itertuples(
    name=None,
    index=False,
  )
)
DEPARTMENT_POPULATION = load_department_population()
DEPARTMENTS = set(load_france_departments().departmentName.unique())

CUM_COLUMNS = [
  "n_covid_deaths",
  "n_covid_healed",
  "n_covid_transfered",
  "n_covid_refused",
]
NCUM_COLUMNS = [
  "n_covid_free",
  "n_ncovid_free",
  "n_covid_occ",
  "n_ncovid_occ",
]
BEDCOUNT_COLUMNS = CUM_COLUMNS + NCUM_COLUMNS
ALL_COLUMNS = (["icu_name", "date", "datetime", "department"] + CUM_COLUMNS +
               NCUM_COLUMNS)
SPREAD_CUM_JUMPS_MAX_JUMP = {
  "n_covid_deaths": 10,
  "n_covid_transfered": 10,
  "n_covid_refused": 10,
  "n_covid_healed": 10,
}


def load_if_not_cached(data_source, cached_data, **kwargs):
  if data_source not in DATA_SOURCES:
    raise ValueError(f"Unknown data source: {data_source}")
  load_data_fun = globals().get(f"load_{data_source}", None)
  if load_data_fun is None:
    raise RuntimeError(
      f"No loading function associated to data source {data_source}"
    )
  if cached_data is None or data_source not in cached_data:
    return load_data_fun(cached_data=cached_data, **kwargs)
  return cached_data[data_source]


def load_bedcounts(
  clean=True,
  spread_cum_jump_correction=False,
  api_key=None,
  icubam_host=None,
  max_date=None,
  cached_data=None,
):
  d = load_if_not_cached(
    "icubam", cached_data, api_key=api_key, icubam_host=icubam_host
  )
  if clean:
    d = clean_data(d, spread_cum_jump_correction)
  d = d.sort_values(by=["date", "icu_name"])
  if max_date is not None:
    logging.info("data loaded's max date will be %s (excluded)" % max_date)
    d = d.loc[d.date < pd.to_datetime(max_date).date()]
  return d


def load_icubam(cached_data=None, api_key=None, icubam_host=None, clean=True):
  if api_key is None or icubam_host is None:
    raise RuntimeError("Provide API key and host to download ICUBAM data")
  else:
    protocol = "http" if icubam_host.startswith("localhost") else "https"
    url = (
      f"{protocol}://{icubam_host}/"
      f"db/all_bedcounts?format=csv&API_KEY={api_key}"
    )
    logging.info("downloading data from %s" % url)
    d = pd.read_csv(url.format(api_key))
  if clean:
    d = d.rename(columns={"create_date": "date", "icu_dept": "department"})
    with open(DATA_PATHS["department_typo_fixes"]) as f:
      department_typo_fixes = json.load(f)
    for wrong_name, right_name in department_typo_fixes.items():
      d.loc[d.department == wrong_name, "department"] = right_name
    d = format_data(d)
  return d


def load_pre_icubam(data_path, cached_data=None):
  d = load_data_file(data_path)
  d = d.rename(
    columns={
      "Hopital": "icu_name",
      "NbSortieVivant": "n_covid_healed",
      "NbCOVID": "n_covid_occ",
      "NbLitDispo": "n_covid_free",
      "NbDeces": "n_covid_deaths",
      "Date": "date",
    }
  )
  fix_icu_names = {
    "C-Scweitzer": "C-Schweitzer",
    "Bezannes": "C-Bezannes",
    "NHC-Chir": "NHC-ChirC",
  }
  for wrong_name, fixed_name in fix_icu_names.items():
    d.loc[d.icu_name == wrong_name, "icu_name"] = fixed_name
  fix_same_icu = {
    "CHR-SSPI": "CHR-Thionville",
    "CHR-CCV": "CHR-Thionville",
    "Nancy-NC": "Nancy-RCP",
  }
  for old, new in fix_same_icu.items():
    d.loc[d.icu_name == old, "icu_name"] = new
  missing_columns = [
    "n_covid_transfered",
    "n_covid_refused",
    "n_ncovid_free",
    "n_ncovid_occ",
  ]
  for col in missing_columns:
    d[col] = 0
  d = format_data(d)
  return d


def format_data(d):
  d["datetime"] = pd.to_datetime(d["date"])
  d["date"] = d["datetime"].dt.date
  d = d[ALL_COLUMNS]
  return d


def clean_data(d, spread_cum_jump_correction=False):
  d.loc[d.icu_name == "Mulhouse-Chir", "n_covid_healed"] = np.clip(
    (
      d.loc[d.icu_name == "Mulhouse-Chir", "n_covid_healed"] -
      d.loc[d.icu_name == "Mulhouse-Chir", "n_covid_transfered"]
    ).values,
    a_min=0,
    a_max=None,
  )
  icu_to_first_input_date = dict(
    d.groupby("icu_name")[["date"]].min().itertuples(name=None)
  )
  d = aggregate_multiple_inputs(d, "15Min") ## merge(15min) + rolling median smoothing + restore monotonic growth in cumu. inputs
  d = fill_in_missing_days(d, "3D") ## if interval > 3D, then fill in by linear interpolation
  d = get_clean_daily_values(d) ## reduces data to 1/day max (and makes it be exactly 1/day)
  if spread_cum_jump_correction: ## pas verifiee ?
    d = spread_cum_jumps(d, icu_to_first_input_date)
  d = d[ALL_COLUMNS]
  return d


def aggregate_multiple_inputs(d, TimeDeltaChosen="15Min"):
  res_dfs = []
  for icu_name, dg in d.groupby("icu_name"):
    dg = dg.set_index("datetime")
    dg = dg.sort_index()

    ## this groups inputs when they are separated bu less than 15 Min in time (in the same ICU)
    mask = ((dg.index.to_series().diff(1) >
             pd.Timedelta(TimeDeltaChosen)).shift(-1).fillna(True).astype(bool))
    dg = dg.loc[mask]

    ## rolling median average, 5 points (for cumulative qtities)
    for col in CUM_COLUMNS:
      dg[col] = (
        dg[col].rolling(5, center=True, min_periods=1).median().astype(int)
      )

    ## rolling median average, 3 points (for non-cumulative qtities)
    for col in NCUM_COLUMNS:
      dg[col] = dg[col].fillna(0)
      dg[col] = (
        dg[col].rolling(3, center=True, min_periods=1).median().astype(int)
      )

    ## si la valeur decroit dans une colonne cumulative, alors on redresse ne appliquant la valeur precedente
    for col in CUM_COLUMNS:
      new_col = []
      last_val = -100000
      for idx, row in dg.iterrows():
        if row[col] >= last_val:
          new_val = row[col]
        else:
          new_val = last_val
        new_col.append(new_val)
        last_val = new_val
      dg[col] = new_col

    res_dfs.append(dg.reset_index())
  return pd.concat(res_dfs)


def fill_in_missing_days(d, TimeDeltaChosen="3D"):
  res_dfs = []
  for icu_name, dg in d.groupby("icu_name"):
    dg = dg.sort_values(by=['datetime'])

    ## looking for time intervals larger than 3 Days (or whatever)
    timeDelta = dg['datetime'].diff(1)
    for i, td in enumerate(timeDelta) :
      if td > pd.Timedelta(TimeDeltaChosen): ## if no input in 3 complete days (72 hours)
        Ndays = td//pd.Timedelta("1D")
        slope = 1.0/Ndays
        val_init = dg.iloc[i-1] # because of the diff, the indexing goes like this
        val_final = dg.iloc[i]

        ## adding intermediate days
        for added_day in range(Ndays):
          added_datetime = val_init.datetime + pd.Timedelta("1D")*added_day
          added_date     = val_init.date     + pd.Timedelta("1D")*added_day
          new_row={'datetime': added_datetime,
          'icu_name': val_init.icu_name,
          'date': added_date,
          'department': val_init.department,
          'n_covid_deaths':     np.round(val_init.n_covid_deaths+     (val_final.n_covid_deaths-     val_init.n_covid_deaths)*     added_day*1.0/Ndays,4),
          'n_covid_healed':     np.round(val_init.n_covid_healed+     (val_final.n_covid_healed-     val_init.n_covid_healed)*     added_day*1.0/Ndays,4),
          'n_covid_transfered': np.round(val_init.n_covid_transfered+ (val_final.n_covid_transfered- val_init.n_covid_transfered)* added_day*1.0/Ndays,4),
          'n_covid_refused':    np.round(val_init.n_covid_refused+    (val_final.n_covid_refused-    val_init.n_covid_refused)*    added_day*1.0/Ndays,4),
          'n_covid_free':       np.round(val_init.n_covid_free+       (val_final.n_covid_free-       val_init.n_covid_free)*       added_day*1.0/Ndays,4),
          'n_ncovid_free':      np.round(val_init.n_ncovid_free+      (val_final.n_ncovid_free-      val_init.n_ncovid_free)*      added_day*1.0/Ndays,4),
          'n_covid_occ':        np.round(val_init.n_covid_occ+        (val_final.n_covid_occ-        val_init.n_covid_occ)*        added_day*1.0/Ndays,4),
          'n_ncovid_occ':       np.round(val_init.n_ncovid_occ+       (val_final.n_ncovid_occ-       val_init.n_ncovid_occ)*       added_day*1.0/Ndays,4)}
          dg = dg.append(pd.Series(new_row), ignore_index=True) ## this is an insert, we will sort by date later.
      # else: time delay short enough, nothing to insert
    dg = dg.sort_values(by=['datetime'])
    res_dfs.append(dg)
  return pd.concat(res_dfs)



## make the data set have exactly 1 data point per day (no more, no less)
## It creates the data point if there is none in that day (by taking the last value that was recorded)
def get_clean_daily_values(d):
  dates = sorted(list(d.date.unique()))
  icu_names = sorted(list(d.icu_name.unique()))
  clean_data_points = list()
  per_icu_prev_data_point = dict()
  icu_name_to_dept = dict(
    d.groupby(['icu_name', 'department']
              ).first().reset_index()[['icu_name', 'department'
                                       ]].itertuples(name=None, index=False)
  )
  for date, icu_name in itertools.product(dates, icu_names):
    sd = d.loc[(d.date == date) & (d.icu_name == icu_name)]
    sd = sd.sort_values(by="datetime")
    new_data_point = {
      "date": date,
      "icu_name": icu_name,
      "department": icu_name_to_dept[icu_name],
      "datetime": date,
    }
    new_data_point.update({col: 0 for col in CUM_COLUMNS})
    new_data_point.update({col: 0 for col in NCUM_COLUMNS})
    if icu_name in per_icu_prev_data_point:
      new_data_point.update({
        col: per_icu_prev_data_point[icu_name][col]
        for col in BEDCOUNT_COLUMNS
      })
    if len(sd) > 0:
      new_data_point.update({
        col: sd[col].iloc[-1]
        for col in BEDCOUNT_COLUMNS
      })
    per_icu_prev_data_point[icu_name] = new_data_point
    clean_data_points.append(new_data_point)
  return pd.DataFrame(clean_data_points)


def spread_cum_jumps(d, icu_to_first_input_date):
  assert np.all(d.date.values == d.datetime.values)
  date_begin_transfered_refused = pd.to_datetime("2020-03-25").date()
  dfs = []
  for icu_name, dg in d.groupby("icu_name"):
    dg = dg.sort_values(by="date")
    dg = dg.reset_index()
    already_fixed_col = set()
    for switch_point, cols in (
      (icu_to_first_input_date[icu_name], CUM_COLUMNS),
      (
        date_begin_transfered_refused,
        ["n_covid_transfered", "n_covid_refused"],
      ),
    ):
      beg = max(
        dg.date.min(),
        switch_point - pd.Timedelta("2D"),
      )
      end = min(
        dg.date.max(),
        switch_point + pd.Timedelta("2D"),
      )
      for col in cols:
        if col in already_fixed_col:
          continue
        beg_val = dg.loc[dg.date == beg, col].values[0]
        end_val = dg.loc[dg.date == end, col].values[0]
        diff = end_val - beg_val
        if diff >= SPREAD_CUM_JUMPS_MAX_JUMP[col]:
          spread_beg = dg.date.min()
          spread_end = end
          spread_range = pd.date_range(spread_beg, spread_end, freq="1D").date
          spread_value = diff // (spread_end - spread_beg).days
          remaining = diff % (spread_end - spread_beg).days
          dg.loc[dg.date.isin(spread_range), col] = np.clip(
            np.cumsum(np.repeat(spread_value, len(spread_range))),
            a_min=0,
            a_max=end_val,
          )
          dg.loc[dg.date == end, col] = np.clip(
            dg.loc[dg.date == end, col].values[0] + remaining,
            a_min=0,
            a_max=end_val,
          )
          already_fixed_col.add(col)
    dfs.append(dg)
  return pd.concat(dfs)


def load_data_file(data_path):
  ext = data_path.rsplit(".", 1)[-1]
  if ext == "pickle":
    with open(data_path, "rb") as f:
      d = pickle.load(f)
  elif ext == "h5":
    d = pd.read_hdf(data_path)
  elif ext == "csv":
    d = pd.read_csv(data_path)
  else:
    raise ValueError(f"unknown extension {ext}")
  return d


def load_public(cached_data=None):
  filename_prefix = "donnees-hospitalieres-covid19-2020"
  logging.info("scrapping public data")
  url = (
    "https://www.data.gouv.fr/fr/datasets/"
    "donnees-hospitalieres-relatives-a-lepidemie-de-covid-19/"
  )
  with urllib.request.urlopen(url) as f:
    html_content = f.read().decode("utf8")
  tree = html.fromstring(html_content)
  download_url = None
  elements = tree.xpath('//*[contains(@class, "resource-card")]')
  for e in elements:
    resource_name = e.xpath('//*[contains(@class, "ellipsis")]/text()')[0]
    if resource_name.startswith(filename_prefix):
      download_url = e.xpath("//*[@download]/@href")[0]
      logging.info("found resource %s at %s" % (resource_name, download_url))
      break
  if download_url is None:
    raise Exception("Could not scrap public data")
  d = pd.read_csv(
    download_url,
    sep=";",
  )
  d = d.loc[d.sexe == 0]  ## load only the data for women+men (all summed)
  d = d.rename(
    columns={
      "dep": "department_code",
      "jour": "date",
      "hosp": "n_hospitalised_patients",
      "rea": "n_icu_patients",
      "rad": "n_hospital_healed",
      "dc": "n_hospital_death",
    }
  )
  d["date"] = pd.to_datetime(d["date"]).dt.date
  return d[[
    "date",
    "department_code",
    "n_hospitalised_patients",
    "n_icu_patients",
    "n_hospital_healed",
    "n_hospital_death",
  ]]


def load_combined_bedcounts_public(api_key=None, cached_data=None, icubam_host=None, **kwargs):
  get_dpt_pop = load_department_population().get
  dp = load_if_not_cached("public", cached_data)
  dp["department"] = dp.department_code.apply(CODE_TO_DEPARTMENT.get)
  di = load_if_not_cached("bedcounts", cached_data, api_key=api_key, icubam_host=icubam_host)
  di = di.groupby(["date", "department"]).sum().reset_index()
  di["department_code"] = di.department.apply(DEPARTMENT_TO_CODE.get)
  di["n_icu_patients"] = di.n_covid_occ + di.n_ncovid_occ.fillna(0)
  combined = di.merge(
    dp, on=["department", "date"], suffixes=["_icubam", "_public"]
  )
  combined["department_pop"] = combined.department.apply(get_dpt_pop)
  return combined


def dep2region(str_dep_code):
  france_dep2region = load_france_departments()
  # regioncode = int(france_dep2region[france_dep2region["departmentCode"]==str(dep_code)]["regionCode"].iloc[0])
  # str_dep_code = str(dep_code)
  # if dep_code<10 :
  #   str_dep_code = '0'+str_dep_code
  if str_dep_code not in france_dep2region.departmentCode.unique() :
    print("ce numero, ", str_dep_code, " n'est pas un numero de departement")
  regioncode = france_dep2region[france_dep2region["departmentCode"]==str_dep_code]["regionCode"]
  return regioncode
# ## test:
# france_dep2region = predicu.data.load_france_departments()
# for dep_code in france_dep2region.departmentCode.unique():
#   # dep_code = int(dep_code)
#   print(dep_code,  predicu.data.dep2region(dep_code))


def region2dep(str_reg_code):
  france_dep2region = load_france_departments()
  # str_reg_code = str(reg_code)
  # if reg_code<10 :
  #   str_reg_code = '0'+str_reg_code
  if str_reg_code not in france_dep2region.regionCode.unique() :
    print("ce numero, ", str_reg_code, " n'est pas un numero de region")
  dep_codes = france_dep2region[france_dep2region["regionCode"]==str_reg_code]["departmentCode"]
  return dep_codes
# ## test:
# france_dep2region = predicu.data.load_france_departments()
# for reg_code in france_dep2region.regionCode.unique():
#   reg_code = int(reg_code)
#   print("region numéro ", reg_code, ", liste des depts: ", predicu.data.region2dep(reg_code))
