import os
from datetime import datetime, timedelta

from flytekit.core.context_manager import FlyteContext

import random
import joblib
import logging
import typing
import pandas as pd
from feast import (
    Entity,
    Feature,
    FeatureStore,
    FeatureView,
    FileSource,
    RepoConfig,
    ValueType,
    online_response,
    registry,
)
from feast.infra.offline_stores.file import FileOfflineStoreConfig
from feast.infra.online_stores.sqlite import SqliteOnlineStoreConfig
from flytekit import reference_task, task, workflow, Workflow
from flytekit.extras.sqlite3.task import SQLite3Config, SQLite3Task
from flytekit.types.file import JoblibSerializedFile
from flytekit.types.file.file import FlyteFile
from flytekit.types.schema import FlyteSchema
from sklearn.model_selection import train_test_split
from sklearn.naive_bayes import GaussianNB
from flytekit.configuration import aws
from feature_eng_tasks import mean_median_imputer, univariate_selection


logger = logging.getLogger(__file__)
# TODO: find a better way to define these features.
FEAST_FEATURES = [
    "horse_colic_stats:rectal temperature",
    "horse_colic_stats:total protein",
    "horse_colic_stats:peripheral pulse",
    "horse_colic_stats:surgical lesion",
    "horse_colic_stats:abdominal distension",
    "horse_colic_stats:nasogastric tube",
    "horse_colic_stats:outcome",
    "horse_colic_stats:packed cell volume",
    "horse_colic_stats:nasogastric reflux PH",
]
DATABASE_URI = "https://cdn.discordapp.com/attachments/545481172399030272/861575373783040030/horse_colic.db.zip"
DATA_CLASS = "surgical lesion"


def _build_feature_store(registry: FlyteFile, online_store_local_path: str = "") -> FeatureStore:
    # TODO: comment this
    if registry.remote_source.startswith("s3://"):
        os.environ["FEAST_S3_ENDPOINT_URL"] = aws.S3_ENDPOINT.get()
        os.environ["AWS_ACCESS_KEY_ID"] = aws.S3_ACCESS_KEY_ID.get()
        os.environ["AWS_SECRET_ACCESS_KEY"] = aws.S3_SECRET_ACCESS_KEY.get()

    config = RepoConfig(
        registry=registry.remote_source,
        project=f"horsecolic",
        # Notice the use of a custom provider.
        provider="custom_provider.provider.FlyteCustomProvider",
        offline_store=FileOfflineStoreConfig(),
        online_store=SqliteOnlineStoreConfig(path=online_store_local_path),
    )
    return FeatureStore(config=config)


sql_task = SQLite3Task(
    name="sqlite3.horse_colic",
    query_template="select * from data",
    output_schema_type=FlyteSchema,
    task_config=SQLite3Config(
        uri=DATABASE_URI,
        compressed=True,
    ),
)


@task
def store_offline(registry: FlyteFile, dataframe: FlyteSchema, feature_store: _FeatureStore) -> FlyteFile:
    horse_colic_entity = Entity(name="Hospital Number", value_type=ValueType.STRING)

    horse_colic_feature_view = FeatureView(
        name="horse_colic_stats",
        entities=["Hospital Number"],
        features=[
            Feature(name="rectal temperature", dtype=ValueType.FLOAT),
            Feature(name="total protein", dtype=ValueType.FLOAT),
            Feature(name="peripheral pulse", dtype=ValueType.FLOAT),
            Feature(name="surgical lesion", dtype=ValueType.STRING),
            Feature(name="abdominal distension", dtype=ValueType.FLOAT),
            Feature(name="nasogastric tube", dtype=ValueType.STRING),
            Feature(name="outcome", dtype=ValueType.STRING),
            Feature(name="packed cell volume", dtype=ValueType.FLOAT),
            Feature(name="nasogastric reflux PH", dtype=ValueType.FLOAT),
        ],
        batch_source=FileSource(
            path=str(dataframe.remote_path),
            event_timestamp_column="timestamp",
        ),
        ttl=timedelta(days=1),
    )

    # Ingest the data into feast
    feature_store.apply([horse_colic_entity, horse_colic_feature_view])

    return FlyteFile(registry.remote_source)


@task
def load_historical_features(registry: FlyteFile, feature_store: _FeatureStore) -> FlyteSchema:
    entity_df = pd.DataFrame.from_dict(
        {
            "Hospital Number": [
                "530101",
                "5290409",
                "5291329",
                "530051",
                "529518",
                "530101",
                "529340",
                "5290409",
                "530034",
            ],
            "event_timestamp": [
                datetime(2021, 6, 25, 16, 36, 27),
                datetime(2021, 6, 25, 16, 36, 27),
                datetime(2021, 6, 25, 16, 36, 27),
                datetime(2021, 6, 25, 16, 36, 27),
                datetime(2021, 6, 25, 16, 36, 27),
                datetime(2021, 7, 5, 11, 36, 1),
                datetime(2021, 6, 25, 16, 36, 27),
                datetime(2021, 7, 5, 11, 50, 40),
                datetime(2021, 6, 25, 16, 36, 27),
            ],
        }
    )

    return feature_store.get_historical_features(
        entity_df=entity_df,
        features=FEAST_FEATURES,
    )


# %%
# Next, we train the Naive Bayes model using the data that's been fetched from the feature store.
@task
def train_model(dataset: pd.DataFrame, data_class: str) -> JoblibSerializedFile:
    X_train, _, y_train, _ = train_test_split(
        dataset[dataset.columns[~dataset.columns.isin([data_class])]],
        dataset[data_class],
        test_size=0.33,
        random_state=42,
    )
    model = GaussianNB()
    model.fit(X_train, y_train)
    model.feature_names = list(X_train.columns.values)
    fname = "/tmp/model.joblib.dat"
    joblib.dump(model, fname)
    return fname

@task
def store_online(registry: FlyteFile, online_store: FlyteFile, feature_store: _FeatureStore) -> (FlyteFile, FlyteFile):
    feature_store.materialize(
        start_date=datetime.utcnow() - timedelta(days=250),
        end_date=datetime.utcnow() - timedelta(minutes=10),
    )

    return registry, online_store

@task
def retrieve_online(
        registry: FlyteFile, online_store: FlyteFile, dataset: pd.DataFrame, feature_store: _FeatureStore
) -> dict:
    inference_data = random.choice(dataset["Hospital Number"])
    logger.info(f"Hospital Number chosen for inference is: {inference_data}")
    entity_rows = [{"Hospital Number": inference_data}]

    return feature_store.get_online_features(FEAST_FEATURES, entity_rows)


# %%
# We define a task to test the model using the inference point fetched earlier.
@task
def test_model(
    model_ser: JoblibSerializedFile,
    inference_point: dict,
) -> typing.List[str]:

    # Load model
    model = joblib.load(model_ser)
    f_names = model.feature_names

    test_list = []
    for each_name in f_names:
        test_list.append(inference_point[each_name][0])
    prediction = model.predict([test_list])
    return prediction


@task
def convert_timestamp_column(
    dataframe: FlyteSchema, timestamp_column: str
) -> FlyteSchema:
    df = dataframe.open().all()
    df[timestamp_column] = pd.to_datetime(df[timestamp_column])
    return df


@workflow
def feast_workflow(
    imputation_method: str = "mean",
    num_features_univariate: int = 7,
    registry: FlyteFile = "s3://feast-integration/registry.db",
) -> typing.List[str]:
    # Load parquet file from sqlite task
    df = sql_task()

    dataframe = mean_median_imputer(dataframe=df, imputation_method=imputation_method)

    # Need to convert timestamp column in the underlying dataframe, otherwise its type is written as
    # string. There is probably a better way of doing this conversion.
    converted_df = convert_timestamp_column(
        dataframe=dataframe, timestamp_column="timestamp"
    )

    feature_store_config = FeatureStoreConfig(s3_bucket='feast-integration', registry_path="registry.db", project="horsecolic", online_store_path="online.db")
    feature_store = _FeatureStore(config=feature_store_config)

    registry_to_historical_features_task = store_offline(
        registry=registry, dataframe=converted_df, feature_store=feature_store
    )

    load_historical_features_node = create_node(load_historical_features, registry=registry_to_historical_features_task, feature_store=feature_store)

    selected_features = univariate_selection(
        dataframe=load_historical_features_node.o0,
        num_features=num_features_univariate,
        data_class=DATA_CLASS,
    )

    trained_model = train_model(
        dataset=selected_features,
        data_class=DATA_CLASS,
    )

    r1, os1 = store_online(registry=registry_to_historical_features_task, online_store="s3://feast-integration/online.db", feature_store=feature_store)

    inference_point = retrieve_online(registry=r1, online_store=os1, dataset=dataframe, feature_store=feature_store)

    prediction = test_model(
        model_ser=trained_model,
        inference_point=inference_point,
    )

    return prediction



if __name__ == "__main__":
    print(f"{feast_workflow(registry='registry.db')}")
