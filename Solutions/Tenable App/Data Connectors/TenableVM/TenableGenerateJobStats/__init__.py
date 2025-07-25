"""Generate Job Stats file."""

import logging
import os
from datetime import datetime, timedelta
from tenable_helper import TenableStatus

from ..exports_store import ExportsTableStore, ExportsTableNames

connection_string = os.environ["AzureWebJobsStorage"]
stats_table_name = ExportsTableNames.TenableExportStatsTable.value
assets_export_table_name = ExportsTableNames.TenableAssetExportTable.value
vuln_export_table_name = ExportsTableNames.TenableVulnExportTable.value
compliance_export_table_name = ExportsTableNames.TenableComplianceExportTable.value
was_asset_export_table_name = ExportsTableNames.TenableWASAssetExportTable.value
was_vuln_export_table_name = ExportsTableNames.TenableWASVulnExportTable.value
ingest_compliance_data = True if os.environ.get("ComplianceDataIngestion", "False").lower() == "true" else False
ingest_was_asset_data = True if os.environ.get("WASAssetDataIngestion", "False").lower() == "true" else False
ingest_was_vuln_data = True if os.environ.get("WASVulnerabilityDataIngestion", "False").lower() == "true" else False
logs_starts_with = "TenableVM"
function_name = "TenableGenerateJobStats"


def generate_finished_stats(table_client: ExportsTableStore, stats_table: ExportsTableStore):
    """
    Retrieve all finished chunks from the ExportsTableStore and update the associated job.

    This function updates the stats_table with the finished chunk IDs and the count of finished chunks.

    Args:
        table_client (ExportsTableStore): The ExportsTableStore with the chunks.
        stats_table (ExportsTableStore): The ExportsTableStore with the jobs and their stats.

    Returns:
        None
    """
    finished_chunks = table_client.query_for_all_finished_chunks()
    jobs_with_finished_chunks = {}

    for fc in finished_chunks:
        logging.info(fc)
        job_id = fc["PartitionKey"]
        chunk_id = fc["RowKey"]
        if fc["PartitionKey"] not in jobs_with_finished_chunks:
            jobs_with_finished_chunks[job_id] = {"chunks": [chunk_id]}
        else:
            jobs_with_finished_chunks[job_id]["chunks"].append(chunk_id)

    for job_id in jobs_with_finished_chunks.keys():
        logging.info(f"{logs_starts_with} {function_name}: Sending finished stats for {job_id}")
        job = stats_table.get(job_id, "prime")
        existing_finished_chunks = job["finishedChunks"] if "finishedChunks" in job else ""

        ids = list(filter(None, existing_finished_chunks.split(",")))
        to_add_ids = jobs_with_finished_chunks[job_id]["chunks"]

        logging.info(f"{logs_starts_with} {function_name}: Checking existing ids: {ids}")
        if sorted(ids) == sorted(to_add_ids):
            logging.info(f"{logs_starts_with} {function_name}: Nothing to update here")
            continue
        else:
            chunk_ids = list(set(ids) | set(to_add_ids))
            chunk_ids_comma_list = ",".join(str(c) for c in chunk_ids)

        logging.info(f"{logs_starts_with} {function_name}: Adding in these chunks {chunk_ids} to finished list")
        chunk_count = len(chunk_ids)
        result = stats_table.merge(
            job_id,
            "prime",
            {"finishedChunks": chunk_ids_comma_list, "finishedChunkCount": chunk_count},
        )
        logging.info(f"{logs_starts_with} {function_name}: Result: {result}")


def create_comma_separated_chunk_ids(chunk_ids):
    """Provide Comma(,) separated chunk_ids.

    Args:
        chunk_ids (list): list of chunk ids

    Returns:
        str: string of comma separated chunk_ids
    """
    chunk_ids_str = ",".join(str(c) for c in chunk_ids)
    return chunk_ids_str


def update_job_time(job, update_job):
    """Update job time if it has not been set or if it has been over 3 days since the job was started.

    Args:
        job (dict): The job to update.
        update_job (dict): A dictionary of updates to make to the job.
    """
    started_at = job["startedAt"] if "startedAt" in job else 0
    if started_at == 0:
        update_job.update({"startedAt": datetime.now().timestamp()})
    else:
        started_at_time = datetime.fromtimestamp(started_at) + timedelta(days=3)
        if started_at_time < datetime.now():
            update_job.update({"status": TenableStatus.failed.value})


def update_processing_chunk_count(jobs_with_processing_chunks, stats_table):
    """Process chunk_ids and their count for processing chunks and update in main table.

    Args:
        jobs_with_processing_chunks (dict): contains job_id and it's processing chunks.
        stats_table (object): Table Storage object for Stats Table.
    """
    for job_id in jobs_with_processing_chunks.keys():
        logging.info(f"{logs_starts_with} {function_name}: Sending processing stats for {job_id}")
        job = stats_table.get(job_id, "prime")
        existing_processing_chunks = job["processingChunks"] if "processingChunks" in job else ""
        existing_finished_chunks = job["finishedChunks"] if "finishedChunks" in job else ""
        existing_failed_chunks = job["failedChunks"] if "failedChunks" in job else ""

        finished_ids = list(filter(None, existing_finished_chunks.split(",")))
        failed_ids = list(filter(None, existing_failed_chunks.split(",")))
        processing_ids = list(filter(None, existing_processing_chunks.split(",")))
        to_add_ids = jobs_with_processing_chunks[job_id]["chunks"]

        chunk_ids = list((set(processing_ids) | set(to_add_ids)) - set(finished_ids) - set(failed_ids))
        chunk_ids_comma_list = create_comma_separated_chunk_ids(chunk_ids)

        logging.info(f"{logs_starts_with} {function_name}: Adding in these chunks {chunk_ids} to processing list")
        update_job = {}
        chunk_count = len(chunk_ids)
        if chunk_count <= 0:
            continue

        update_job_time(job, update_job)

        update_job.update(
            {
                "processingChunks": chunk_ids_comma_list,
                "processingChunkCount": chunk_count,
            }
        )
        result = stats_table.merge(job_id, "prime", update_job)
        logging.info(f"{logs_starts_with} {function_name}: Result: {result}")


def generate_processing_stats(table_client: ExportsTableStore, stats_table: ExportsTableStore):
    """Retrieve all processing chunks and update the associated job.

    This function queries the ExportsTableStore for processing chunks and updates the stats_table
    with the processing chunk IDs and their count.

    Args:
        table_client (ExportsTableStore): The ExportsTableStore with the chunks.
        stats_table (ExportsTableStore): The ExportsTableStore with the jobs and their stats.

    Returns:
        None
    """
    processing_chunks = table_client.query_for_all_queued_chunks()
    jobs_with_processing_chunks = {}

    for pc in processing_chunks:
        logging.info(pc)
        job_id = pc["PartitionKey"]
        chunk_id = pc["RowKey"]
        if pc["PartitionKey"] not in jobs_with_processing_chunks:
            jobs_with_processing_chunks[job_id] = {"chunks": [chunk_id]}
        else:
            jobs_with_processing_chunks[job_id]["chunks"].append(chunk_id)

    update_processing_chunk_count(jobs_with_processing_chunks, stats_table)


def generate_failed_stats(table_client: ExportsTableStore, stats_table: ExportsTableStore):
    """
    Retrieve all failed chunks from the ExportsTableStore and update the associated job.

    This function updates the stats_table with the failed chunk IDs and the count of failed chunks.

    Args:
        table_client (ExportsTableStore): The ExportsTableStore with the chunks.
        stats_table (ExportsTableStore): The ExportsTableStore with the jobs and their stats.

    Returns:
        None
    """
    failed_chunks = table_client.query_for_all_failed_chunks()
    jobs_with_failed_chunks = {}

    for fc in failed_chunks:
        logging.info(fc)
        job_id = fc["PartitionKey"]
        chunk_id = fc["RowKey"]
        if fc["PartitionKey"] not in jobs_with_failed_chunks:
            jobs_with_failed_chunks[job_id] = {"chunks": [chunk_id], "failedCount": 1}
        else:
            jobs_with_failed_chunks[job_id]["chunks"].append(chunk_id)
            jobs_with_failed_chunks[job_id]["failedCount"] += 1

    for job_id in jobs_with_failed_chunks.keys():
        logging.info(f"{logs_starts_with} {function_name}: Sending failure stats for {job_id}")
        job = stats_table.get(job_id, "prime")
        existing_failed_chunks = job["failedChunks"] if "failedChunks" in job else ""

        ids = list(filter(None, existing_failed_chunks.split(",")))
        to_add_ids = jobs_with_failed_chunks[job_id]["chunks"]

        logging.info(f"{logs_starts_with} {function_name}: Checking existing ids: {ids}")
        if sorted(ids) == sorted(to_add_ids):
            logging.info(f"{logs_starts_with} {function_name}: Nothing to update here")
            continue
        else:
            chunk_ids = list(set(ids) | set(to_add_ids))
            chunk_ids_comma_list = ",".join(str(c) for c in chunk_ids)

        logging.info(f"{logs_starts_with} {function_name}: Adding in these chunks {chunk_ids} to failure list")
        update_job = {}
        chunk_count = len(chunk_ids)

        if chunk_count > 0:
            update_job["status"] = TenableStatus.failed.value

        update_job.update({"failedChunks": chunk_ids_comma_list, "failedChunkCount": chunk_count})
        result = stats_table.merge(job_id, "prime", update_job)
        logging.info(result)


def main(name) -> str:
    """
    Execute the main entry point for the TenableGenerateJobStats function.

    This function generates finished, failed, and processing stats for all jobs
    in the assets, vuln.
    Compliance tables if ingest_compliance_data is True and WAS Vuln tables if ingest_was_vuln_data is True.

    Returns:
        True
    """
    stats_table = ExportsTableStore(connection_string, stats_table_name)
    assets_table = ExportsTableStore(connection_string, assets_export_table_name)
    vuln_table = ExportsTableStore(connection_string, vuln_export_table_name)
    compliance_table = ExportsTableStore(connection_string, compliance_export_table_name)
    was_asset_table = ExportsTableStore(connection_string, was_asset_export_table_name)
    was_vuln_table = ExportsTableStore(connection_string, was_vuln_export_table_name)

    generate_finished_stats(assets_table, stats_table)
    generate_finished_stats(vuln_table, stats_table)

    generate_failed_stats(assets_table, stats_table)
    generate_failed_stats(vuln_table, stats_table)

    generate_processing_stats(assets_table, stats_table)
    generate_processing_stats(vuln_table, stats_table)

    if ingest_compliance_data:
        generate_finished_stats(compliance_table, stats_table)
        generate_failed_stats(compliance_table, stats_table)
        generate_processing_stats(compliance_table, stats_table)

    if ingest_was_asset_data:
        generate_finished_stats(was_asset_table, stats_table)
        generate_failed_stats(was_asset_table, stats_table)
        generate_processing_stats(was_asset_table, stats_table)

    if ingest_was_vuln_data:
        generate_finished_stats(was_vuln_table, stats_table)
        generate_failed_stats(was_vuln_table, stats_table)
        generate_processing_stats(was_vuln_table, stats_table)
    return "True"
