"""Video Analysis: play clips, plot intensity chronographs, and optionally mark a solenoid cycle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QSplitter

from lib.paths import list_video_clips, video_campaign_dir, videos_dir

from ..campaign_import import load_video_module
from ..registry import FAMILY_ANALYSIS, AnalysisSpec, get, register
from ..widgets.chronograph import ChronoTrace, ChronographPlot, read_chronograph
from ..widgets.solenoid_frames import SolenoidFramesDialog, pack_frames
from ..widgets.solenoid_panel import SolenoidPanel
from ..widgets.video_browser import VideoBrowser
from ..widgets.video_player import VideoPlayer
from ..window import AnalysisWindow
from ..workers import WorkerHandle

VIDEO_ANALYSIS_ID = "video_analysis"

_SELECT_CLIP = (
    "Select a clip to play it. "
    "Shift-click or Command-click overlays chronographs."
)


class _ExtractProgress(QObject):
    """Cross-thread progress. ``report`` is safe to call from the worker."""

    advanced = Signal(float, int, str)

    def report(self, completed: float, total: int, name: str) -> None:
        self.advanced.emit(completed, total, name)


def _extract_campaign(campaign: str, progress: _ExtractProgress) -> dict[str, Any]:
    extract = load_video_module("extract_brightness")
    clips = list_video_clips(campaign)
    total = len(clips)
    done: list[tuple[str, float | None]] = []
    for index, clip in enumerate(clips):
        progress.report(float(index), total, clip.name)

        def on_frame(
            frames_done: int,
            frames_expected: int,
            *,
            clip_index: int = index,
            clip_name: str = clip.name,
        ) -> None:
            if frames_expected <= 0:
                fraction = 0.0
            else:
                fraction = min(0.99, frames_done / frames_expected)
            progress.report(clip_index + fraction, total, clip_name)

        _csv, _meta, meta = extract.ensure_cache(clip, force=False, on_frame=on_frame)
        t1 = meta.get("t1_s")
        done.append((clip.name, None if t1 is None else float(t1)))
        progress.report(float(index + 1), total, clip.name)
    return {"campaign": campaign, "clips": done}


def _sample_frames(video: str, times: dict[str, Any]) -> dict[str, Any]:
    solenoid = load_video_module("solenoid")
    return pack_frames(solenoid.sample_windows(Path(video), times))


class VideoAnalysisWindow(AnalysisWindow):
    def __init__(self, spec: AnalysisSpec, controller: Any) -> None:
        self._loaded: Path | None = None
        self._extracting = False
        self._extract_worker = WorkerHandle()
        self._frame_worker = WorkerHandle()
        self._sampling = False
        self._frame_generation = 0
        self._solenoid_key: tuple[str | None, str | None] | None = None
        super().__init__(spec, controller)
        self._extract_progress = _ExtractProgress(self)
        self._extract_progress.advanced.connect(self._on_extract_progress)
        self.resize(1200, 860)
        self._show_idle()

    def _build_menu(self) -> None:
        super()._build_menu()
        file_menu = self.menuBar().actions()[0].menu()
        assert file_menu is not None
        for action in file_menu.actions():
            if action.text() == "Refresh catalogue":
                action.setText("Refresh videos")

    def _build_body(self) -> None:
        self._browser = VideoBrowser()
        self._browser.current_clip_changed.connect(self._on_clip)
        self._browser.selection_changed.connect(self._on_selection)
        self._player = VideoPlayer()
        self._player.status_changed.connect(self._on_player_status)
        self._chrono = ChronographPlot()
        self._chrono.extract_requested.connect(self._extract_current_campaign)
        self._chrono.edge_moved.connect(self._on_edge_moved)
        self._solenoid = SolenoidPanel()
        self._solenoid.enable_requested.connect(self._enable_solenoid)
        self._solenoid.disable_requested.connect(self._disable_solenoid)
        self._solenoid.times_edited.connect(self._on_solenoid_edited)
        self._solenoid.frames_requested.connect(self._on_frames)
        self._solenoid.status_message.connect(self.statusBar().showMessage)

        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self._player)
        right.addWidget(self._solenoid)
        right.addWidget(self._chrono)
        right.setStretchFactor(0, 2)
        right.setStretchFactor(1, 0)
        right.setStretchFactor(2, 2)
        right.setSizes([340, 230, 290])

        splitter = QSplitter()
        splitter.addWidget(self._browser)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([420, 780])
        self.setCentralWidget(splitter)

    def _refresh_catalogue(self) -> None:
        self._loaded = None
        self._solenoid_key = None
        self._browser.refresh()
        if self._browser.current_clip() is None:
            self._show_idle()
        else:
            self._update_plot()

    def _on_player_status(self, text: str) -> None:
        self.statusBar().showMessage(text)

    def _on_clip(self, path: Path | None) -> None:
        self._chrono.set_campaign(self._browser.current_campaign())
        self._sync_solenoid()
        if path is None:
            self._loaded = None
            self._show_idle()
            return
        if path != self._loaded:
            self._loaded = path
            self._player.load(path)
        self._update_plot()

    def _on_selection(self) -> None:
        self._chrono.set_campaign(self._browser.current_campaign())
        self._update_plot()

    def _show_idle(self) -> None:
        self._chrono.set_campaign(self._browser.current_campaign())
        self._sync_solenoid()
        if self._browser.has_campaigns():
            self._player.show_message(_SELECT_CLIP)
            self._update_plot()
            if self._browser.current_clip() is None:
                self.statusBar().showMessage("Select a clip")
            return
        root = videos_dir()
        self._player.show_message(
            "No video campaigns yet.\n\n"
            f"Create a folder under {root}/<campaign>/ and put MP4 or MOV files "
            "directly in it, then choose File → Refresh videos."
        )
        self._chrono.set_windows([], [])
        self._chrono.show_message("Add a campaign folder to plot chronographs.")
        self.statusBar().showMessage("No video campaigns")

    def _clips_to_plot(self) -> list[Path]:
        selected = self._browser.selected_clips()
        if selected:
            return sorted(selected, key=lambda path: (path.parent.name.lower(), path.name.lower()))
        current = self._browser.current_clip()
        return [current] if current is not None else []

    def _update_plot(self) -> None:
        dataset = load_video_module("dataset")
        traces: list[ChronoTrace] = []
        missing = 0
        for clip in self._clips_to_plot():
            csv_path, _meta_path = dataset.video_to_cache(clip)
            if not csv_path.is_file():
                missing += 1
                continue
            try:
                time_s, intensity = read_chronograph(csv_path)
            except (OSError, RuntimeError, ValueError):
                missing += 1
                continue
            traces.append(
                ChronoTrace(
                    label=clip.stem,
                    time_s=time_s,
                    intensity=intensity,
                    t1_s=dataset.rising_edge_s(clip),
                )
            )
        windows, markers = self._solenoid_marks()
        if not traces and missing == 0:
            self._chrono.set_windows([], [])
            self._chrono.show_message("Select a clip to plot its chronograph.")
        else:
            self._chrono.set_windows(windows, markers)
            self._chrono.set_traces(traces, missing=missing)
        self._apply_solenoid_readout()

    def _extract_current_campaign(self) -> None:
        campaign = self._browser.current_campaign()
        if not campaign or self._extracting:
            return
        self._extracting = True
        self._chrono.set_extracting(True)
        self.statusBar().showMessage(f"Extracting {campaign}…")
        self._extract_worker.start(
            _extract_campaign,
            campaign,
            self._extract_progress,
            on_finished=self._on_extracted,
            on_failed=self._on_extract_failed,
        )

    def _on_extract_progress(self, completed: float, total: int, name: str) -> None:
        if not self._extracting:
            return
        self._chrono.set_progress(completed, total, name)
        if name:
            self.statusBar().showMessage(f"Extracting {name}")

    def _on_extracted(self, result: object) -> None:
        self._extracting = False
        self._chrono.set_extracting(False)
        payload = result if isinstance(result, dict) else {}
        campaign = str(payload.get("campaign", ""))
        count = len(payload.get("clips", []))
        self._browser.refresh()
        self.statusBar().showMessage(f"Extracted {campaign}: {count} clips")

    def _on_extract_failed(self, message: str) -> None:
        self._extracting = False
        self._chrono.set_extracting(False)
        self._chrono.set_campaign(self._browser.current_campaign())
        self.statusBar().showMessage(message)

    def _on_edge_moved(self, t1_s: float) -> None:
        selected = self._browser.selected_clips()
        clip = selected[0] if len(selected) == 1 else self._browser.current_clip()
        if clip is None:
            return
        try:
            load_video_module("dataset").set_rising_edge(clip, t1_s)
        except (OSError, ValueError) as exc:
            self.statusBar().showMessage(str(exc))
            return
        self._solenoid.set_rising_edge(t1_s)
        self.statusBar().showMessage(f"{clip.name}: rising edge {t1_s:.4g} s")

    def _sync_solenoid(self, *, force: bool = False) -> None:
        campaign = self._browser.current_campaign()
        clip = self._browser.current_clip()
        stem = None if clip is None else clip.stem
        key = (campaign, stem)
        if not force and key == self._solenoid_key:
            return
        self._solenoid_key = key
        cycle = None
        if campaign:
            try:
                cycle = load_video_module("solenoid").load_cycle(video_campaign_dir(campaign))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self.statusBar().showMessage(str(exc))
        edge = _rising_edge(clip)
        self._solenoid.set_context(campaign, stem, cycle, edge)

    def _enable_solenoid(self) -> None:
        campaign = self._browser.current_campaign()
        if not campaign:
            return
        solenoid = load_video_module("solenoid")
        directory = video_campaign_dir(campaign)
        try:
            existing = solenoid.load_cycle(directory)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.statusBar().showMessage(str(exc))
            return
        if existing is None:
            edge = _rising_edge(self._browser.current_clip())
            solenoid.save_cycle(directory, solenoid.new_cycle(edge))
            if edge is None:
                self.statusBar().showMessage(f"{campaign}: solenoid timing on")
            else:
                self.statusBar().showMessage(
                    f"{campaign}: solenoid timing on, tube on {edge:.4g} s"
                )
        self._sync_solenoid(force=True)
        self._update_plot()

    def _disable_solenoid(self) -> None:
        campaign = self._browser.current_campaign()
        if not campaign:
            return
        load_video_module("solenoid").delete_cycle(video_campaign_dir(campaign))
        self.statusBar().showMessage(f"{campaign}: solenoid timing removed")
        self._sync_solenoid(force=True)
        self._update_plot()

    def _on_solenoid_edited(self, clip_stem: object, times: dict[str, Any]) -> None:
        campaign = self._browser.current_campaign()
        if not campaign:
            return
        try:
            load_video_module("solenoid").write_scope(
                video_campaign_dir(campaign),
                times,
                clip_stem=None if clip_stem is None else str(clip_stem),
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.statusBar().showMessage(str(exc))
            return
        self._update_plot()

    def _solenoid_marks(
        self,
    ) -> tuple[list[tuple[str, float, float, str]], list[tuple[str, float]]]:
        clips = self._clips_to_plot()
        if len(clips) != 1:
            return [], []
        solenoid = load_video_module("solenoid")
        try:
            cycle = solenoid.load_cycle(clips[0].parent)
        except (OSError, ValueError, json.JSONDecodeError):
            return [], []
        if cycle is None:
            return [], []
        return solenoid.plot_marks(solenoid.resolved_times(cycle, clips[0].stem))

    def _apply_solenoid_readout(self) -> None:
        solenoid = load_video_module("solenoid")
        campaign = self._browser.current_campaign()
        clip = self._browser.current_clip()
        self._solenoid.set_rising_edge(_rising_edge(clip))
        if not campaign or clip is None:
            self._solenoid.set_summary("")
            self._solenoid.set_frames_enabled(False)
            return
        try:
            cycle = solenoid.load_cycle(video_campaign_dir(campaign))
        except (OSError, ValueError, json.JSONDecodeError):
            self._solenoid.set_summary("")
            self._solenoid.set_frames_enabled(False)
            return
        if cycle is None:
            self._solenoid.set_summary("")
            self._solenoid.set_frames_enabled(False)
            return
        times = solenoid.resolved_times(cycle, clip.stem)
        time_s, intensity = _chronograph_arrays(clip)
        text = solenoid.intensity_summary(time_s, intensity, times)
        if time_s is None and text:
            text += "\nExtract a chronograph to compute window means."
        if len(self._clips_to_plot()) > 1 and text:
            text = "Bands are drawn when one clip is plotted.\n" + text
        self._solenoid.set_summary(text)
        self._solenoid.set_frames_enabled(_has_current_windows(solenoid, times))

    def _on_frames(self) -> None:
        if self._sampling or not self._solenoid.commit():
            return
        clip = self._browser.current_clip()
        if clip is None:
            return
        solenoid = load_video_module("solenoid")
        try:
            cycle = solenoid.load_cycle(clip.parent)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.statusBar().showMessage(str(exc))
            return
        if cycle is None:
            return
        times = solenoid.resolved_times(cycle, clip.stem)
        if not _has_current_windows(solenoid, times):
            self.statusBar().showMessage(
                "Set tube on, current on, and current off before reading frames."
            )
            return
        self._frame_generation += 1
        generation = self._frame_generation
        self._sampling = True
        self._solenoid.set_frames_busy(True)
        self.statusBar().showMessage(f"Reading frames in {clip.name}")
        self._frame_worker.start(
            _sample_frames,
            str(clip),
            times,
            on_finished=lambda result, generation=generation, clip=clip: self._on_frames_ready(
                generation, clip, result
            ),
            on_failed=lambda message, generation=generation: self._on_frames_failed(
                generation, message
            ),
        )

    def _on_frames_ready(self, generation: int, clip: Path, result: object) -> None:
        if generation != self._frame_generation:
            return
        self._sampling = False
        self._solenoid.set_frames_busy(False)
        self._apply_solenoid_readout()
        if not isinstance(result, dict):
            return
        self._frames_dialog = SolenoidFramesDialog(clip.name, result, self)
        self._frames_dialog.show()
        self.statusBar().showMessage(f"{clip.name}: off / on frames")

    def _on_frames_failed(self, generation: int, message: str) -> None:
        if generation != self._frame_generation:
            return
        self._sampling = False
        self._solenoid.set_frames_busy(False)
        self._apply_solenoid_readout()
        self.statusBar().showMessage(message)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._frame_generation += 1
        self._extract_worker.cancel()
        self._frame_worker.cancel()
        self._player.stop()
        super().closeEvent(event)


def _chronograph_arrays(clip: Path) -> tuple[Any, Any]:
    dataset = load_video_module("dataset")
    csv_path, _meta_path = dataset.video_to_cache(clip)
    if not csv_path.is_file():
        return None, None
    try:
        return read_chronograph(csv_path)
    except (OSError, RuntimeError, ValueError):
        return None, None


def _rising_edge(clip: Path | None) -> float | None:
    if clip is None:
        return None
    try:
        return load_video_module("dataset").rising_edge_s(clip)
    except (OSError, ValueError):
        return None


def _has_current_windows(solenoid: Any, times: dict[str, Any]) -> bool:
    roles = {window.role for window in solenoid.phase_windows(times)}
    return "off" in roles and "on" in roles


def _create_window(controller: Any) -> VideoAnalysisWindow:
    return VideoAnalysisWindow(get(VIDEO_ANALYSIS_ID), controller)


register(
    AnalysisSpec(
        id=VIDEO_ANALYSIS_ID,
        title="Video Analysis",
        description=(
            "Play clips under Measurements/Videos and plot intensity chronographs. "
            "Overlay several clips, aligned on the rising edge or in raw time. "
            "A campaign can be run as a solenoid measurement with editable tube and current times."
        ),
        family=FAMILY_ANALYSIS,
        window_factory=_create_window,
    )
)
