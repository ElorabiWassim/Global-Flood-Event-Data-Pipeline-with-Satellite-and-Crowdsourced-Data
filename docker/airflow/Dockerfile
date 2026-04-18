FROM apache/airflow:2.10.5-python3.12

ENV PYTHONPATH=/opt/airflow:/opt/airflow/project

COPY requirements/base.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
