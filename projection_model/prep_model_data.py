"""
This code defines functions for reading raw CSV data pulled from the webscraper
and cleans it into data frame formats that can be fed into a predictinve ML mode.
The outputs from this model are:
- train_df.csv
- val_df.csv
- test_df.csv
Each dataframe will have all of the variables (columns) and samplevplayer-weeks
(rows) required to train/fit/predict/evaluate a given dataset.
"""

import pandas as pd
import pickle
import os
import re
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
import gc
import fnmatch

class globs():
    dir_player = "../data/player_weeks/"
    dir_opp = "../data/opp_weeks/"
    dir_salaries = "../data/fanduel_salaries/"
    dir_nflweather = "../data/nfl_weather/"
    dir_snapcounts = "../data/snapcounts/"
    #dir_benchmark = "../data/fanduel_projections/" # TODO: need to get the scraper run for 2019 fanduel projections
    dir_benchmark = "../data/espn_projections/" # ESPN's PPR projections (only rostered players)
    dir_model = "../data/model_data/"

    file_team_rename_map = "../meta_data/team_rename_map.csv"
    file_weather_rename_map = "../meta_data/weather_team_rename_map.csv"

    file_opp = "opp_stats_{}.csv"
    file_player = "player_stats_{}.csv"
    file_salaries = "fd_salaries_{}.csv"
    file_snapcounts = "snapcounts_stats_{}.csv"
    file_benchmark = "espn_proj_2019.csv"

    file_model_data = "df_model_{}.csv"
    file_df_train = "df_train.csv"
    file_df_val = "df_val.csv"
    file_df_test = "df_test.csv"

    INCLUDE_POSITIONS = ['QB', 'TE', 'WR', 'RB']
    #YEARS = [2013,2014,2015,2016,2017,2018,2019]
    #YEARS = [2013,2014,2016,2018,2019]
    YEARS = [2016,2017,2018,2019]
    TRAIN_YRS = [2016,2017]
    VAL_YRS = [2018]
    TEST_YRS = [2019]

    # Player stats used to generate features
    stat_cols = [
        'fumbles_lost', 'fumbles_rcv', 'fumbles_tot','fumbles_trcv', 'fumbles_yds',
        'passing_att', 'passing_cmp', 'passing_ints', 'passing_tds', 'passer_ratio',
        'passing_twopta', 'passing_twoptm', 'passing_yds',
        'puntret_tds','puntret_avg', 'puntret_lng', 'puntret_lngtd', 'puntret_ret',
        'receiving_lng', 'receiving_lngtd','receiving_rec', 'receiving_tds', 'receiving_twopta',
        'receiving_twoptm', 'receiving_yds', 'rushing_att', 'rushing_lng',
        'rushing_lngtd', 'rushing_tds', 'rushing_twopta', 'rushing_twoptm',
        'rushing_yds','fantasy_points',
        'PassRushRatio_Att','PassRushRatio_Yds','PassRushRatio_Tds','RushRecRatio_AttRec',
        'RushRecRatio_Tds','RushRecRatio_Yds'
    ]

    model_features = [
        "QB", "RB", "TE", "WR", "height", "weight", "age", "week", "fantasy_points",
        ""
    ]

def trim_sort(df):
    df = df.sort_values(['id','week'])
    df = df[globs.stat_cols+['id','week','team','position','full_name']]
    return df

def get_trend(df_in):
    """Compute a three-week trend for each game statistic, for each player."""
    # Drop non-ID identifier columns
    drop_cols = ["team", "position", "full_name"]
    groupby_cols = ["id"]
    df = df_in[[c for c in df_in if c not in drop_cols]]

    # compute 3-week and 2-week points deltas
    deltas = df.groupby(groupby_cols).pct_change()
    deltas = deltas.add_prefix('chg_')
    deltas = pd.concat([df, deltas], axis=1)
    deltas2 = deltas.groupby(groupby_cols)[deltas.columns].shift(1).fillna(0)
    deltas3 = deltas.groupby(groupby_cols)[deltas.columns].shift(2).fillna(0)
    deltas2 = deltas2.add_prefix('per2_')
    deltas3 = deltas3.add_prefix('per3_')
    trend_df = pd.concat([deltas, deltas2, deltas3], axis=1)
    # average prior three deltas to get trend
    for col in globs.stat_cols:
        name = 'trend_'+col
        trend_df[name] = trend_df[['chg_'+col,'per2_chg_'+col,'per3_chg_'+col]].mean(axis=1).fillna(0)
    return trend_df

def get_cumul_mean_stats(df, weeks):
    """Create a rolling mean for each statistic by player, by week."""
    weeks_stats_mean = []
    for week in weeks:
        tmp = df[df.week <= week]
        tmp = tmp.groupby(['id'])[globs.stat_cols].mean().reset_index()
        tmp = tmp.add_suffix('_mean')
        tmp['week'] = week
        weeks_stats_mean.append(tmp)
    cumavg_stats = pd.concat(weeks_stats_mean)
    cumavg_stats = cumavg_stats.rename(columns={'id_mean':'id'})
    return cumavg_stats

def get_cumul_stats_time_weighted(df, weeks):
    """Create a rolling time-wegihted mean for each statistic by player, by week."""
    weeks_stats_mean_wgt = []
    for week in weeks:
        tmp1 = df[df.week <= week]
        mult = lambda x: np.asarray(x) * np.asarray(tmp1.week)
        tmp = tmp1[['id']+globs.stat_cols].set_index('id').apply(mult).reset_index()
        tmp = tmp.groupby(['id'])[globs.stat_cols].mean().reset_index()
        tmp = tmp.add_suffix('_wgtmean')
        tmp['week'] = week
        weeks_stats_mean_wgt.append(tmp)
    cumavg_stats_wgt = pd.concat(weeks_stats_mean_wgt)
    cumavg_stats_wgt = cumavg_stats_wgt.rename(columns={'id_wgtmean':'id'})
    return cumavg_stats_wgt

def defensive_ptsallow(matchups, weeks, weighted=False):
    """
    Compute the mean weekly points given up by each defense to each position.
    Parameters:
        matchups: dataframe of matchups between offensive player, and
                  defensive opponent.
        weeks:    list of weeks in the season.
        weighted: boolean. If true, compute weekly points allowed according
                  to player-weighted fantasy points.
    """
    agg_col = 'fantasy_points'
    output_name = 'defensive_matchup_allowed'
    if weighted:
        agg_col = 'weighted_fantasy_points'
        output_name = 'defensive_matchup_allowed_wgt'
    # compute weekly cumulative mean points allowed by each defense
    defense_ranks_dfs = []
    for week in weeks:
        matchweek = matchups[matchups.week <= week]
        # weekly sum of pts allowed by a given defense to each position
        weekly_sums = matchweek.groupby(['week','defense','position'])[agg_col].sum().reset_index()
        # season-to-date mean of weekly sums for each position
        defense_pts_allowed = weekly_sums.groupby(['defense','position'])[agg_col].mean().reset_index()
        defense_pts_allowed = defense_pts_allowed.rename(columns={agg_col:output_name})
        defense_pts_allowed['week'] = week
        defense_ranks_dfs.append(defense_pts_allowed)
    defense_ranks = pd.concat(defense_ranks_dfs)
    return defense_ranks

def weekly_player_weights(matchups, weeks):
    """
    Calculate season-to-date (STD) weekly fantasy points rankings by position.
    """
    player_weights = []
    for week in weeks:
        mask = (matchups.week <= week)
        # each player's mean fantasy points STD
        std_mean = matchups[mask][['id','team','position','fantasy_points','defense']]
        std_mean = std_mean.groupby(['position','id'], as_index=False).mean()

        # each player's weight in a given week with respect to their position.
        # This is the STD mean max-normalized for the current week.
        week_max_position = std_mean.groupby('position', as_index=False).max()
        week_max_position = week_max_position[['position','fantasy_points']]
        week_max_position.columns = ['position','fp_max']
        weekly_weights = std_mean.merge(week_max_position,how='left',on='position')
        weekly_weights['player_weight'] = weekly_weights['fantasy_points'] / weekly_weights['fp_max']
        weekly_weights['week'] = week
        player_weights.append(weekly_weights)
    player_weights = pd.concat(player_weights)
    return player_weights[['id','week','position','player_weight']]

class RenameMap():
    def __init__(self, filepath):
        df = pd.read_csv(filepath, index_col=0)
        self.rename_map = df.to_dict()[df.columns[0]]

class WeeklyStatsYear():
    """
    This class is holds a year's worth of weekly stats. The stats stored in This
    class store all of the variable inputs (X's) as well as the targets (y's),
    and in some cases, the bench mark (y_bench's) that are fed into the ML
    prediction model.
    """
    def __init__(self, year, fpath_player, fpath_opp, fpath_salaries, fpath_snapcounts, dir_nflweather):
        self.year = year
        self.fpath_player = fpath_player
        self.fpath_opp = fpath_opp
        self.fpath_salaries = fpath_salaries
        self.fpath_snapcounts = fpath_snapcounts
        self.dir_nflweather = dir_nflweather

    def read_player_data(self, filepath):
        self.df_player = pd.read_csv(filepath)
        team_rename_map = RenameMap(globs.file_team_rename_map).rename_map
        self.df_player["team"] = self.df_player["team"].replace(team_rename_map)
        if "position_fill" in self.df_player:
            del self.df_player["position_fill"]
        self.df_player = self.df_player.rename(columns={"name": "full_name"})
        self.df_player = self.df_player.reset_index()

    def read_opp_data(self, filepath):
        self.df_opp = pd.read_csv(filepath)
        team_rename_map = RenameMap(globs.file_team_rename_map).rename_map
        self.df_opp["opp_TEAM"] = self.df_opp["opp_TEAM"].replace(team_rename_map)
        self.df_opp["opp_OPP"] = self.df_opp["opp_OPP"].replace(team_rename_map)
        if "position_fill" in self.df_opp:
            del self.df_opp["position_fill"]
        self.df_opp["year"] = self.year
        self.df_opp = self.df_opp.reset_index()
        opp_cols_rename_dict = {
            "opp_week": "week",
            "opp_TEAM": "offense",
            "opp_OPP": "defense",
            "opp_opp_points": "opp_points",
            "opp_first_downs": "opp_first_downs",
            "opp_total_yds": "opp_total_yds",
            "opp_passing_yds": "opp_passing_yds",
            "opp_rushing_yds": "opp_rushing_yds",
            "opp_penalty_yds": "opp_penalty_yds",
            "opp_penalty_cnt": "opp_penalty_cnt",
            "opp_turnovers": "opp_turnovers",
            "opp_punt_cnt": "opp_punt_cnt",
            "opp_punt_yds": "opp_punt_yds",
            "opp_punt_avg": "opp_punt_avg",
            "opp_pos_time": "opp_pos_time",
            "year": "year"
        }
        self.df_opp = self.df_opp.rename(columns=opp_cols_rename_dict)

    def calc_target(self):
        """
        Create fantasy_points (the target variable) according to a
        standard scoring regime
        """
        self.df_player['fantasy_points'] = (self.df_player['passing_tds'] * 4) +\
        (self.df_player['passing_yds'] * 0.04) +\
        (self.df_player['passing_twoptm'] * 2) +\
        (self.df_player['passing_ints'] * -2) +\
        (self.df_player['rushing_tds'] * 6) +\
        (self.df_player['rushing_yds'] * 0.1) +\
        (self.df_player['rushing_twoptm'] * 2) +\
        (self.df_player['receiving_tds'] * 6) +\
        (self.df_player['receiving_yds'] * 0.1) +\
        (self.df_player['receiving_twoptm'] * 2) +\
        (self.df_player['kickret_tds'] * 6) +\
        (self.df_player['puntret_tds'] * 6) +\
        (self.df_player['fumbles_lost'] * -2)

    def calc_target_PPR(self):
        """
        Create fantasy_poiints (the target variable) according to a PPR scoring
        regime.
        """
        self.df_player['fantasy_points'] = (self.df_player['passing_tds'] * 4) +\
        (self.df_player['passing_yds'] * 0.04) +\
        (self.df_player['passing_twoptm'] * 2) +\
        (self.df_player['passing_ints'] * -2) +\
        (self.df_player['rushing_tds'] * 6) +\
        (self.df_player['rushing_yds'] * 0.1) +\
        (self.df_player['rushing_twoptm'] * 2) +\
        (self.df_player['receiving_tds'] * 6) +\
        (self.df_player['receiving_yds'] * 0.1) +\
        (self.df_player['receiving_twoptm'] * 2) +\
        (self.df_player['kickret_tds'] * 6) +\
        (self.df_player['puntret_tds'] * 6) +\
        (self.df_player['fumbles_lost'] * -2) +\
        (self.df_player['receiving_rec']) # Add in the 1 point per reception

    def calc_target_fanduel(self):
        """
        Creates fantasy_points (the target variable) according to a FanDuel's
        scoring regime.
        TODO: Need to look this up, and alter code to use fanduel projections
        benchmark and fanduel targets.
        """
        pass

    def calc_ratios(self):
        """
        Create pass/rus/reception ratios to be included in feature set for model.
        """
        self.df_player['passer_ratio'] = self.df_player['passing_cmp']/self.df_player['passing_att']
        self.df_player['PassRushRatio_Att'] = self.df_player['rushing_att'] / self.df_player['passing_att']
        self.df_player['PassRushRatio_Yds'] = self.df_player['rushing_yds'] / self.df_player['passing_yds']
        self.df_player['PassRushRatio_Tds'] = self.df_player['rushing_tds'] / self.df_player['passing_tds']
        self.df_player['RushRecRatio_AttRec'] = self.df_player['rushing_att'] / self.df_player['receiving_rec']
        self.df_player['RushRecRatio_Tds'] = self.df_player['rushing_tds'] / self.df_player['receiving_tds']
        self.df_player['RushRecRatio_Yds'] = self.df_player['rushing_yds'] / self.df_player['receiving_yds']

    def clean_positions(self):
        """
        Trim the dataset to include the four main offensive positions: QB, RB, WR, TE
        """
        # Determine position_fill for players missing positions
        missing = self.df_player[self.df_player.position.isna()]
        missing = missing.groupby(["id", "full_name"], as_index=False).mean()
        missing = missing[["id", "full_name", "passing_att", "rushing_att", "receiving_rec"]]
        missing.columns = ["id", "full_name", "QB", "RB", "WRTE"]
        missing = missing[(missing.QB!=0) | (missing.RB!=0) | (missing.WRTE!=0)]
        missing["position_fill"] = missing[["QB", "RB", "WRTE"]].idxmax(axis=1)
        missing = missing[["id", "position_fill"]]
        #missing["position_fill"] = missing["position_fill"].apply(lambda x: np.nan if x=="WRTE" else x)
        missing["position_fill"] = missing["position_fill"].apply(lambda x: "WR" if x=="WRTE" else x) # Assuming all WRTE's are WR instead of dropping

        # Impute position based on 'position_fill'
        self.df_player = self.df_player.merge(missing, how='left', on='id')
        self.df_player['position'].fillna(self.df_player['position_fill'], inplace=True)
        #self.df_player['position_fill'] = self.df_player['full_name'].apply(lambda x: fill_positions(x)) # This is left over from non-working function in old code
        self.df_player['position'].fillna(self.df_player['position_fill'], inplace=True)

        # Trim dataset to INCLUDE_POSITIONS
        self.df_player['position'] = self.df_player['position'].str.replace('FB','RB')
        self.df_player = self.df_player[self.df_player['position'].isin(globs.INCLUDE_POSITIONS)]

    # Feature Engineering Helper Functions


    def create_nfl_features(self):

        """Wrapper function that calls all helpers to create custom player and team
           defense stats. This function will return a new dataframe that has merged
           all of the custom stats described in the helper functions for each player.

           Parameters:
                       self.df_player: game summary actuals for each player weekly
                       self.df_opp:    matchups for each game (can substitute with
                                          a schedule with player_id, offense, defense, week)"""

        player_stats_trimmed = trim_sort(self.df_player)
        weeks = sorted(player_stats_trimmed.week.unique().tolist())

        # create offensive player stats
        trend_df         = get_trend(player_stats_trimmed)
        cumavg_stats     = get_cumul_mean_stats(player_stats_trimmed, weeks)
        cumavg_stats_wgt = get_cumul_stats_time_weighted(player_stats_trimmed, weeks)

        # create matchups and defensive opponent stats
        matchup_cols = ['id', 'week', 'team','position', 'full_name', 'offense', 'defense','fantasy_points']
        sched             = self.df_opp[['offense','defense','week']]
        matchups          = player_stats_trimmed.merge(sched, how='left',
                                                       left_on=['week','team'],
                                                       right_on=['week','offense'])[matchup_cols]
        defense_ranks     = defensive_ptsallow(matchups, weeks)
        player_weights    = weekly_player_weights(matchups, weeks)
        player_weights['inverse'] = 1/player_weights.player_weight
        matchups_wgts     = matchups.merge(player_weights, how='left', on=['id','week','position'])
        matchups_wgts['weighted_fantasy_points'] = matchups_wgts['fantasy_points'] * matchups_wgts['inverse']
        defense_ranks_wgt = defensive_ptsallow(matchups_wgts, weeks, weighted=True)

        ## merge features

        # shift target variable, week, and defensive opponent
        matchups['target_defense'] = matchups.sort_values(['id','week']).groupby('id')['defense'].shift(-1)
        matchups['target'] = matchups.sort_values(['id','week']).groupby('id')['fantasy_points'].shift(-1)
        matchups['target_week'] = matchups.sort_values(['id','week']).groupby('id')['week'].shift(-1)

        # drop week 1
        matchups.dropna(inplace=True)

        ## fill in zeros for players with missing historical stats
        matchups.fillna(0, inplace=True)

        # merge player weights to player performances
        matchups = matchups.merge(player_weights, on=['id','week','position'])

        # merge defense rankings to player performances
        defense_ranks_all = defense_ranks.merge(defense_ranks_wgt, on=['defense', 'position', 'week'])
        defense_ranks_all = defense_ranks_all.rename(columns={'defense':'target_defense'})
        matchups = matchups.merge(defense_ranks_all, how='left', on=['target_defense','position','week']).dropna()

        # merge trend, average, and weighted avg stats to player performances
        avgs = cumavg_stats.merge(cumavg_stats_wgt,how='inner',on=['id','week'])
        matchups = matchups.merge(avgs, how='left', on=['id','week'])
        trend_cols = ['id','week']+[col for col in trend_df if col not in matchups.columns]
        td = trend_df[trend_cols]
        matchups = matchups.merge(td, how='inner', on=['id','week'])
        for col in trend_df.columns:
            matchups[col].fillna(0, inplace=True)

        # create extra player attributes and merge to make model-ready df
        attribs = ['id','birthdate','years_pro','height','weight','position','profile_url','last_name','number']
        #player_attributes = self.df_player[attribs]
        player_attributes = self.df_player[[c for c in self.df_player if c in attribs]]
        player_attributes.drop_duplicates(['id'],inplace=True)
        player_attributes['birthdate'] = pd.to_datetime(player_attributes['birthdate'])
        player_attributes['age'] = player_attributes['birthdate'].apply(lambda x: (datetime.today() - x).days/365)
        position_dummies = pd.get_dummies(player_attributes['position'])
        player_attributes = pd.concat([position_dummies, player_attributes], axis=1).drop(['position'],axis=1)

        # final cleaning
        self.df_model = player_attributes.merge(matchups, how='right',on='id')
        self.df_model.replace([-np.inf,np.inf], 0, inplace=True)
        self.df_model["year"] = self.year # For some reason 'year' gets dropped in this function
        return self.df_model

    def read_salaries_data(self, filepath):
        self.df_salaries = pd.read_csv(filepath)
        self.df_salaries['FirstName'] = self.df_salaries['FirstName'].str.strip()
        self.df_salaries['LastName'] = self.df_salaries['LastName'].str.strip()
        self.df_salaries['full_name'] = self.df_salaries['LastName']+' '+self.df_salaries['FirstName']
        self.df_salaries = self.df_salaries[self.df_salaries.Pos.isin(globs.INCLUDE_POSITIONS)].fillna(0)
        self.df_salaries = self.df_salaries[['Week','Team','full_name','fd_points','fd_salary']]
        self.df_salaries.columns = ['week','team','full_name','fd_points','fd_salary']
        self.df_salaries['week'] = pd.to_numeric(self.df_salaries['week'])
        self.df_salaries['fd_points'] = pd.to_numeric(self.df_salaries['fd_points'])
        self.df_salaries['fd_salary'] = pd.to_numeric(self.df_salaries['fd_salary'])
        self.df_salaries["team"] = self.df_salaries["team"].str.upper()
        team_rename_map = RenameMap(globs.file_team_rename_map).rename_map
        self.df_salaries["team"] = self.df_salaries["team"].replace(team_rename_map)

    def merge_salaries(self):
        self.df_model = self.df_model.merge(self.df_salaries, on=["week","full_name","team"], how="left")
        self.df_model = self.df_model.fillna(0)

    def read_snapcounts_data(self, filepath):
        self.df_snapcounts = pd.read_csv(filepath)

    def merge_snapcounts(self):
        self.df_model = self.df_model.merge(self.df_snapcounts, on=["full_name", "week", "year"], how="left")

    def read_weather_data(self, dir_nflweather):
        team_rename_map = RenameMap(globs.file_weather_rename_map).rename_map
        weather_files = os.listdir(dir_nflweather)
        weather_dfs = []
        for fn in weather_files:
            week = re.findall('_[0-9]+\.',fn)
            week = re.sub('[^0-9]','',str(week))
            year = fn[:4]
            if  int(year) == int(self.year):
                df = pd.read_csv(os.path.join(dir_nflweather, fn))
                df['week'] = int(week)
                df['year'] = int(year)
                weather_dfs.append(df)
        self.df_weather = pd.concat(weather_dfs)

        self.df_weather['team1'] = self.df_weather['team1'].apply(lambda x: team_rename_map[x])
        self.df_weather['team2'] = self.df_weather['team2'].apply(lambda x: team_rename_map[x])

        self.df_weather['wind_conditions'] = pd.to_numeric(self.df_weather['wind_conditions'].str.replace('[^0-9]',''))
        self.df_weather['indoor_outdoor'] = self.df_weather['weather_forecast'].apply(lambda x: 1 if 'DOME' in x else 0)

        weather1 = self.df_weather[['team1','wind_conditions','indoor_outdoor','week','year']]
        weather1.columns = ['team','wind_conditions','indoor_outdoor','week','year']
        weather2 = self.df_weather[['team2','wind_conditions','indoor_outdoor','week','year']]
        weather2.columns = ['team','wind_conditions','indoor_outdoor','week','year']
        self.df_weather = pd.concat([weather1,weather2])

    def merge_weather(self):
        self.df_model = self.df_model.merge(self.df_weather, on=["team", "week", "year"], how="left")

    def prep_model_data(self):
        self.read_player_data(self.fpath_player)
        self.read_opp_data(self.fpath_opp)
        self.read_salaries_data(self.fpath_salaries)
        self.calc_target_PPR()
        self.calc_ratios()
        self.clean_positions()
        self.create_nfl_features()
        self.merge_salaries()
        # self.read_snapcounts_data(self.fpath_snapcounts)
        # self.merge_snapcounts()
        self.read_weather_data(self.dir_nflweather)
        self.merge_weather()

    def export_model_data(self):
        savepath = os.path.join(globs.dir_model, globs.file_model_data.format(self.year))
        self.df_model.to_csv(savepath, index=False)

class TrainDataset():
    def __init__(self, all_stats, pos, years):
        self.all_stats = all_stats
        self.pos = pos
        self.years = years

    def subset_data(self):
        self.df_model = [stats_year.df_model for stats_year in self.all_stats if stats_year.year in self.years]
        self.df_model = pd.concat(self.df_model)

class ValDataset():
    def __init__(self, all_stats, pos, years):
        self.all_stats = all_stats
        self.pos = pos
        self.years = years

    def subset_data(self):
        self.df_model = [stats_year.df_model for stats_year in self.all_stats if stats_year.year in self.years]
        self.df_model = pd.concat(self.df_model)

class MLDataset():
    def __init__(self, all_data, pos, train_yrs, val_yrs, test_yrs):
        self.all_data = all_data
        self.pos = pos
        self.train_yrs = train_yrs
        self.val_yrs = val_yrs
        self.test_yrs = test_yrs

    def split_train_val_test(self):
        self.df_train = [data_yr.df_model for data_yr in self.all_data if data_yr.year in self.train_yrs]
        self.df_train = pd.concat(self.df_train)

        self.df_val = [data_yr.df_model for data_yr in self.all_data if data_yr.year in self.val_yrs]
        self.df_val = pd.concat(self.df_val)

        self.df_test = [data_yr.df_model for data_yr in self.all_data if data_yr.year in self.test_yrs]
        self.df_test = pd.concat(self.df_test)

    def subset_position(self):
        self.df_train = self.df_train[self.df_train[self.pos]==1]
        self.df_val = self.df_val[self.df_val[self.pos]==1]
        self.df_test = self.df_test[self.df_test[self.pos]==1]

    def read_espn_benchmark(self, filepath):
        df = pd.read_csv(filepath)
        rename_dict = {
            "Week": "week",
            "year": "year",
            "Name": "full_name",
            "Pos": "position",
            "proj_espn_ppr": "benchmark"
        }
        df = df.rename(columns=rename_dict)

        # Rename the Jr, Sr, III, II
        drop_suffix_dict = {" Jr": "", " Sr": "", " III": "", " II": ""}
        df["full_name"] = df["full_name"].replace(drop_suffix_dict, regex=True)

        df = df[rename_dict.values()]
        self.df_test = self.df_test.merge(df, on=["week", "year", "full_name", "position"], how="left")

    def read_fantasydata_benchmark(self, filepath):
        pass

    def trim_low_scores(self):
        self.df_train = self.df_train[self.df_train.target > 0]
        self.df_val = self.df_val[self.df_val.target > 0]
        self.df_test = self.df_test[self.df_test.target > 0]

    def get_all_features(self):
        qb_features = [c for c in self.df_train if fnmatch.fnmatch(c, "passing*_wgt*mean")]
        qb_features += [c for c in self.df_train if fnmatch.fnmatch(c, "passer*_wgt*mean")]
        rb_features = [c for c in self.df_train if fnmatch.fnmatch(c, "rushing*_wgt*mean")]
        wr_features = [c for c in self.df_train if fnmatch.fnmatch(c, "receiving*_wgt*mean")]
        def_features = [c for c in self.df_train if fnmatch.fnmatch(c, "defensive*_wgt*mean")]
        fumble_features = [c for c in self.df_train if fnmatch.fnmatch(c, "fumbles*_wgt*mean")]
        trend_features = [c for c in self.df_train if c.startswith("trend_")]

        shared_features =[
            "QB", "WR", "TE", "RB", "fd_salary", "wind_conditions",
            "indoor_outdoor", "target_week", "inverse"
        ]
        self.all_features = shared_features +\
            qb_features + rb_features + wr_features +\
            def_features + fumble_features + trend_features

        target_col = ["target"]
        benchmark_col = ["benchmark"]

        self.df_train = self.df_train[self.all_features + target_col]
        self.df_val = self.df_val[self.all_features + target_col]
        self.df_test = self.df_test[self.all_features + target_col + benchmark_col]

    def export_datasets(self):
        # Print export datsets sizes
        print("Train Shape: {}".format(self.df_train.shape))
        print("Val Shape: {}".format(self.df_val.shape))
        print("Test Shape: {}".format(self.df_test.shape))

        # Define savepaths
        savepath_train = os.path.join(globs.dir_model, globs.file_df_train)
        savepath_val = os.path.join(globs.dir_model, globs.file_df_val)
        savepath_test = os.path.join(globs.dir_model, globs.file_df_test)

        # Save Data
        self.df_train.to_csv(savepath_train, index=False)
        self.df_val.to_csv(savepath_val, index=False)
        self.df_test.to_csv(savepath_test, index=False)

if __name__ == "__main__":
    # Prep Yearly Stats
    stats_yrs = []
    for year in globs.YEARS:
        print("Preprocessing Stats Years: {}".format(year))
        stats_yr = WeeklyStatsYear(
            year,
            os.path.join(globs.dir_player, globs.file_player.format(year)),
            os.path.join(globs.dir_opp, globs.file_opp.format(year)),
            os.path.join(globs.dir_salaries, globs.file_salaries.format(year)),
            os.path.join(globs.dir_snapcounts, globs.file_snapcounts.format(year)),
            globs.dir_nflweather
        )
        stats_yr.prep_model_data()
        stats_yr.export_model_data()
        stats_yrs.append(stats_yr)

    # Prep Train/Val/Test Splits
    ml_dataset = MLDataset(
        stats_yrs,
        "all",
        globs.TRAIN_YRS,
        globs.VAL_YRS,
        globs.TEST_YRS
    )
    ml_dataset.split_train_val_test()
    ml_dataset.read_espn_benchmark(os.path.join(globs.dir_benchmark, globs.file_benchmark))
    ml_dataset.trim_low_scores()
    ml_dataset.get_all_features()
    ml_dataset.export_datasets()

    '''
    ml_datasets = {}
    for pos in globs.INCLUDE_POSITIONS:
        print("Generating ML Datasets by Position: {}".format(pos))
        ml_datasets[pos] = MLDataset(
            stats_yrs,
            pos,
            globs.TRAIN_YRS,
            globs.VAL_YRS,
            globs.TEST_YRS
        )
        ml_datasets[pos].split_train_val_test()
        ml_datasets[pos].subset_position()
        ml_datasets[pos].read_espn_benchmark(os.path.join(globs.dir_benchmark, globs.file_benchmark))
        ml_datasets[pos].get_all_features()
    '''
