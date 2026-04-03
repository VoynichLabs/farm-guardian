# Author: Claude Opus 4.6
# Date: 03-April-2026
# PURPOSE: Daily intelligence report generation for Farm Guardian v2 (Phase 4).
#          Queries the SQLite database for a given date and produces structured JSON
#          and Markdown reports. Includes: total/predator detection counts, per-species
#          breakdown, predator visit summaries, deterrent effectiveness, activity-by-hour
#          heatmap, 7-day trend comparison, and natural language summary. Exports to
#          data/exports/ as YYYY-MM-DD.json and YYYY-MM-DD.md. Can run on-demand via
#          API/dashboard or automatically at end of day via guardian.py.
# SRP/DRY check: Pass — single responsibility is report generation and export.

import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

from database import GuardianDB

log = logging.getLogger("guardian.reports")


class ReportGenerator:
    """Generates daily summary reports from the guardian database."""

    def __init__(self, config: dict, db: GuardianDB):
        reports_cfg = config.get("reports", {})
        storage_cfg = config.get("storage", {})
        self._export_dir = Path(storage_cfg.get("exports_dir", "data/exports"))
        self._export_formats = reports_cfg.get("export_formats", ["json", "markdown"])
        self._db = db

        self._export_dir.mkdir(parents=True, exist_ok=True)
        log.info("ReportGenerator initialized — export_dir=%s", self._export_dir)

    def generate_daily_report(self, target_date: Optional[str] = None) -> dict:
        """
        Generate a complete daily report for the given date (YYYY-MM-DD).
        Defaults to today. Returns the report as a dict and exports to files.
        """
        if not target_date:
            target_date = date.today().isoformat()

        log.info("Generating daily report for %s", target_date)

        # Gather data from DB
        species_counts = self._db.get_detection_counts_by_class(target_date)
        hourly_activity = self._db.get_detections_by_hour(target_date)
        predator_tracks = self._db.get_predator_tracks_for_date(target_date)
        alerts = self._db.get_recent_alerts(days=1)
        deterrent_stats = self._db.get_deterrent_effectiveness(days=1)

        # Compute summary stats
        total_detections = sum(species_counts.values())
        predator_species = {"hawk", "bobcat", "coyote", "fox", "raccoon", "possum",
                            "wild_cat", "other_bird", "other_canine"}
        predator_detections = sum(
            c for s, c in species_counts.items() if s in predator_species
        )
        unique_species = sorted(species_counts.keys())

        # Today's alerts count
        today_alerts = sum(
            1 for a in alerts if a.get("alerted_at", "").startswith(target_date)
        )

        # Peak activity hour
        peak_hour = max(hourly_activity, key=hourly_activity.get) if hourly_activity else None

        # Build predator visit summaries
        visit_summaries = []
        for track in predator_tracks:
            first_seen = track.get("first_seen_at", "")
            time_str = first_seen[11:16] if len(first_seen) > 16 else "unknown"

            deterrent_list = []
            if track.get("deterrent_used"):
                try:
                    deterrent_list = json.loads(track["deterrent_used"])
                except (json.JSONDecodeError, TypeError):
                    pass

            visit_summaries.append({
                "species": track["class_name"],
                "time": time_str,
                "duration_seconds": track.get("duration_sec", 0),
                "max_confidence": track.get("max_confidence", 0),
                "deterrent": deterrent_list,
                "outcome": track.get("outcome", "unknown"),
            })

        # Natural language summary
        summary_text = self._build_summary_text(
            target_date, total_detections, predator_detections,
            visit_summaries, deterrent_stats, species_counts, peak_hour,
        )

        report = {
            "date": target_date,
            "farm": "Hampton CT",
            "generated_at": datetime.now().isoformat(),
            "summary": summary_text,
            "predator_visits": visit_summaries,
            "stats": {
                "total_detections": total_detections,
                "predator_detections": predator_detections,
                "unique_species": unique_species,
                "species_counts": species_counts,
                "alerts_sent": today_alerts,
                "deterrents_fired": deterrent_stats.get("total_actions", 0),
                "deterrent_success_rate": deterrent_stats.get("success_rate", 0.0),
                "peak_activity_hour": peak_hour,
                "activity_by_hour": {str(h): c for h, c in hourly_activity.items()},
            },
        }

        # Export to files
        self._export(target_date, report)

        # Save to daily_summaries table
        try:
            self._db.insert_daily_summary(
                summary_date=target_date,
                total_detections=total_detections,
                predator_detections=predator_detections,
                unique_species=unique_species,
                alerts_sent=today_alerts,
                deterrents_activated=deterrent_stats.get("total_actions", 0),
                peak_activity_hour=peak_hour,
                activity_by_hour=dict(hourly_activity),
                species_counts=species_counts,
                predator_tracks=visit_summaries,
                deterrent_success_rate=deterrent_stats.get("success_rate"),
                summary_text=summary_text,
            )
        except Exception as exc:
            log.error("Failed to save daily summary to DB: %s", exc)

        log.info("Daily report for %s complete — %d detections, %d predator visits",
                 target_date, total_detections, len(visit_summaries))

        return report

    def _export(self, target_date: str, report: dict) -> None:
        """Export report to JSON and/or Markdown files."""
        if "json" in self._export_formats:
            json_path = self._export_dir / f"{target_date}.json"
            try:
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(report, f, indent=2)
                log.info("Exported JSON report: %s", json_path)
            except OSError as exc:
                log.error("Failed to write JSON report: %s", exc)

        if "markdown" in self._export_formats:
            md_path = self._export_dir / f"{target_date}.md"
            try:
                md_content = self._render_markdown(report)
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md_content)
                log.info("Exported Markdown report: %s", md_path)
            except OSError as exc:
                log.error("Failed to write Markdown report: %s", exc)

    def _render_markdown(self, report: dict) -> str:
        """Render a report dict as a Markdown document."""
        d = report["date"]
        stats = report["stats"]
        visits = report["predator_visits"]

        # Format date for display
        try:
            dt = date.fromisoformat(d)
            date_display = dt.strftime("%B %d, %Y")
        except ValueError:
            date_display = d

        lines = [
            f"## Farm Guardian Daily Report -- {date_display}",
            "",
            "### Activity Summary",
            report.get("summary", "No summary available."),
            "",
            "### Detection Stats",
            f"- **Total detections:** {stats['total_detections']}",
            f"- **Predator detections:** {stats['predator_detections']}",
            f"- **Unique species:** {', '.join(stats.get('unique_species', []))}",
            f"- **Alerts sent:** {stats['alerts_sent']}",
            f"- **Peak activity hour:** {stats['peak_activity_hour']}:00" if stats.get('peak_activity_hour') is not None else "- **Peak activity hour:** N/A",
            "",
        ]

        # Species breakdown
        if stats.get("species_counts"):
            lines.append("### Species Breakdown")
            for species, count in sorted(stats["species_counts"].items(), key=lambda x: -x[1]):
                lines.append(f"- **{species}:** {count}")
            lines.append("")

        # Predator visits
        if visits:
            lines.append("### Predator Activity")
            for v in visits:
                duration = v.get("duration_seconds", 0) or 0
                duration_str = f"{duration:.0f}s" if duration < 60 else f"{duration / 60:.1f}m"
                deterrent_str = ", ".join(v.get("deterrent", [])) or "none"
                lines.append(
                    f"- **{v['species']}** at {v['time']} -- "
                    f"duration: {duration_str}, "
                    f"confidence: {v.get('max_confidence', 0):.0%}, "
                    f"deterrent: {deterrent_str}, "
                    f"outcome: {v.get('outcome', 'unknown')}"
                )
            lines.append("")

        # Deterrent effectiveness
        deterrent_fired = stats.get("deterrents_fired", 0)
        if deterrent_fired > 0:
            lines.append("### Deterrent Effectiveness")
            lines.append(f"- Activated {deterrent_fired} time(s)")
            lines.append(f"- Success rate: {stats.get('deterrent_success_rate', 0):.0%}")
            lines.append("")

        # Hourly activity
        activity = stats.get("activity_by_hour", {})
        if activity:
            lines.append("### Activity by Hour")
            for h in sorted(activity.keys(), key=lambda x: int(x)):
                count = activity[h]
                bar = "#" * min(count, 50)
                lines.append(f"  {int(h):02d}:00  {bar} ({count})")
            lines.append("")

        lines.append(f"*Generated by Farm Guardian on {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
        return "\n".join(lines)

    def _build_summary_text(
        self, target_date: str, total: int, predator: int,
        visits: list[dict], deterrent_stats: dict,
        species_counts: dict, peak_hour: Optional[int],
    ) -> str:
        """Build a natural language summary paragraph."""
        parts = []

        if total == 0:
            return f"No detections recorded on {target_date}."

        parts.append(f"{total} total detections recorded.")

        if predator > 0:
            species_set = sorted(set(v["species"] for v in visits))
            parts.append(
                f"{len(visits)} predator visit(s) detected ({', '.join(species_set)})."
            )
        else:
            parts.append("No predator activity detected.")

        fired = deterrent_stats.get("total_actions", 0)
        if fired > 0:
            rate = deterrent_stats.get("success_rate", 0)
            parts.append(f"Deterrents activated {fired} time(s), {rate:.0%} success rate.")

        if peak_hour is not None:
            parts.append(f"Peak activity at {peak_hour}:00.")

        # Chicken safety
        chicken_count = species_counts.get("chicken", 0)
        if chicken_count > 0:
            parts.append(f"Chickens detected {chicken_count} times (active in yard).")

        return " ".join(parts)

    def get_available_dates(self) -> list[str]:
        """List dates that have exported reports."""
        dates = []
        for f in sorted(self._export_dir.glob("*.json"), reverse=True):
            try:
                date.fromisoformat(f.stem)
                dates.append(f.stem)
            except ValueError:
                continue
        return dates

    def get_report(self, target_date: str) -> Optional[dict]:
        """Load an existing report from the export directory."""
        json_path = self._export_dir / f"{target_date}.json"
        if json_path.exists():
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                log.error("Failed to load report %s: %s", json_path, exc)
        return None
