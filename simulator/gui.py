"""PyQt6 desktop app for the external JDSS simulator.

Pick a scenario (airborne assault on Eben-Emael, or the beach landing at Narvik), choose a
**secure** (PSK / HMAC-SHA256) or **non-secure** (null) connection, and Start. The simulation
loops continuously: units patrol their routes and stream JDSS traffic to the gateway, while the
app shows each unit's live position and the messages going out and coming in.

The async engine runs in a worker thread; it pushes events over a Qt signal, so the UI stays
responsive. Run with ``python -m simulator``.
"""

from __future__ import annotations

import asyncio
import threading
import time

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from simulator.engine import SimulatorEngine
from simulator.scenarios import SCENARIOS

_TYPE_COLOR = {
    "Presence": "#3a78c2",
    "Identification": "#8e7cc3",
    "ContactSighting": "#c0392b",
    "CasevacRequest": "#e08a1e",
    "Chat": "#3aa76d",
    "Overlay": "#a0a04a",
}


class SimWorker(QThread):
    """Runs the async :class:`SimulatorEngine` in its own event loop, looping until stopped."""

    event = pyqtSignal(dict)

    def __init__(self, scenario_key: str, interval: float, **kwargs) -> None:
        super().__init__()
        self._scenario = SCENARIOS[scenario_key]
        self._interval = max(0.1, interval)
        self._kwargs = kwargs
        self._stop = threading.Event()

    def run(self) -> None:  # QThread entry point
        try:
            asyncio.run(self._main())
        except Exception as exc:  # surface any startup failure in the UI
            self.event.emit({"kind": "status", "text": f"fatal: {exc}"})

    async def _main(self) -> None:
        engine = SimulatorEngine(self._scenario, on_event=self.event.emit, **self._kwargs)
        try:
            await engine.start()
            tick = 0
            while not self._stop.is_set():
                await engine.tick(self._interval, tick)
                tick += 1
                await asyncio.sleep(self._interval)
        finally:
            await engine.stop()

    def stop(self) -> None:
        self._stop.set()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("JDSS External Simulator — coalition scenarios")
        self.resize(1180, 720)
        self._worker: SimWorker | None = None
        self._rows: dict[str, int] = {}
        self._build()

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.addWidget(self._controls())

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self._unit_table())
        split.addWidget(self._logs())
        split.setSizes([560, 620])
        outer.addWidget(split, 1)

        self.statusBar().showMessage("Idle — pick a scenario and Start.")

    def _controls(self) -> QWidget:
        box = QGroupBox("Connection")
        form = QFormLayout(box)

        self.scenario = QComboBox()
        for key, sc in SCENARIOS.items():
            self.scenario.addItem(sc.name, key)
        self.scenario.currentIndexChanged.connect(self._scenario_changed)

        self.secure = QCheckBox("Secure — PSK / HMAC-SHA256 (uncheck for non-secure/null)")
        self.secure.setChecked(True)

        self.transport = QComboBox()
        self.transport.addItems(["udp", "loopback"])
        self.transport.setToolTip(
            "udp = join a real gateway's multicast network; loopback = isolated"
        )

        self.codec = QComboBox()
        self.codec.addItems(["xml", "json", "arrow"])

        self.network_id = QLineEdit()
        self.psk = QLineEdit("jdss-coalition-key")
        self.classification = QComboBox()
        self.classification.addItems(["0 UNCLASS", "1 RESTRICTED", "2 CONFIDENTIAL", "3 SECRET"])
        self.classification.setCurrentIndex(1)

        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.1, 10.0)
        self.interval.setSingleStep(0.1)
        self.interval.setValue(1.0)
        self.interval.setSuffix(" s / tick")

        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self._start)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self._stop)
        self.stop_btn.setEnabled(False)

        row1 = QHBoxLayout()
        row1.addWidget(self.transport)
        row1.addWidget(self.codec)
        row1.addWidget(self.classification)
        row1.addWidget(self.interval)
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.stop_btn)
        buttons.addStretch(1)

        form.addRow("Scenario", self.scenario)
        form.addRow("Security", self.secure)
        form.addRow("Transport / codec / class / rate", _wrap(row1))
        form.addRow("Network id", self.network_id)
        form.addRow("Coalition PSK", self.psk)
        form.addRow("", _wrap(buttons))
        self._scenario_changed()
        return box

    def _unit_table(self) -> QWidget:
        box = QGroupBox("Units")
        lay = QVBoxLayout(box)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Callsign", "Nation", "Role", "Lat", "Lon", "Crs", "Spd", "Sent"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table)
        return box

    def _logs(self) -> QWidget:
        tabs = QTabWidget()
        self.sent_log = QPlainTextEdit()
        self.sent_log.setReadOnly(True)
        self.sent_log.setMaximumBlockCount(2000)
        self.recv_log = QPlainTextEdit()
        self.recv_log.setReadOnly(True)
        self.recv_log.setMaximumBlockCount(2000)
        tabs.addTab(self.sent_log, "Outgoing (JDSS → gateway)")
        tabs.addTab(self.recv_log, "Incoming (from coalition)")
        return tabs

    # ------------------------------------------------------------------ control
    def _scenario_changed(self) -> None:
        sc = SCENARIOS[self.scenario.currentData()]
        self.network_id.setText(sc.network_id)
        self.statusBar().showMessage(sc.description)

    def _start(self) -> None:
        self.table.setRowCount(0)
        self._rows.clear()
        self.sent_log.clear()
        self.recv_log.clear()
        self._worker = SimWorker(
            self.scenario.currentData(),
            interval=self.interval.value(),
            secure=self.secure.isChecked(),
            transport=self.transport.currentText(),
            codec=self.codec.currentText(),
            network_id=self.network_id.text().strip() or None,
            psk=self.psk.text(),
            classification=self.classification.currentIndex(),
        )
        self._worker.event.connect(self._on_event)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._set_inputs(False)

    def _stop(self) -> None:
        if self._worker is not None:
            self.stop_btn.setEnabled(False)
            self._worker.stop()

    def _on_finished(self) -> None:
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._set_inputs(True)
        self._worker = None

    def _set_inputs(self, on: bool) -> None:
        for w in (self.scenario, self.secure, self.transport, self.codec,
                  self.network_id, self.psk, self.classification, self.interval):
            w.setEnabled(on)

    # ------------------------------------------------------------------ events
    def _on_event(self, e: dict) -> None:
        kind = e.get("kind")
        if kind == "unit":
            self._update_unit(e)
        elif kind == "sent":
            self._append_sent(e)
        elif kind == "received":
            self._append_recv(e)
        elif kind == "stats":
            self._update_stats(e)
        elif kind == "status":
            self.statusBar().showMessage(e["text"])
            self.sent_log.appendPlainText(f"— {e['text']}")

    def _update_unit(self, e: dict) -> None:
        nid = e["node_id"]
        if nid not in self._rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self._rows[nid] = r
            for c in range(8):
                self.table.setItem(r, c, QTableWidgetItem(""))
        r = self._rows[nid]
        vals = [e["callsign"], e["nation"], e["role"], f"{e['lat']:.4f}",
                f"{e['lon']:.4f}", f"{e['course']:.0f}", f"{e['speed']:.1f}", str(e["sent"])]
        for c, v in enumerate(vals):
            self.table.item(r, c).setText(v)

    def _append_sent(self, e: dict) -> None:
        ts = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
        line = f"{ts}  ▲ {e['callsign']:<12} {e['type']:<15} {e['detail']}"
        self.sent_log.appendPlainText(line)

    def _append_recv(self, e: dict) -> None:
        ts = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
        cs = f" ({e['callsign']})" if e.get("callsign") else ""
        self.recv_log.appendPlainText(f"{ts}  ▼ {e['from']}{cs:<12} {e['type']}")

    def _update_stats(self, e: dict) -> None:
        self.statusBar().showMessage(
            f"tick {e['tick']} · {e['nodes']} units · {e['sent']} sent · {e['received']} received"
        )


def _wrap(layout) -> QWidget:
    w = QWidget()
    w.setLayout(layout)
    return w


def main() -> int:
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
