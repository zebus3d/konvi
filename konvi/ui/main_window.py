"""Ventana principal: destinos arriba (como AVS), perfil, cola y «Convertir»."""

import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
                               QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMenu,
                               QMessageBox, QProgressBar, QPushButton, QSizePolicy, QStackedWidget,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import naming, presets
from ..core.jobs import ACTIVE, EDITABLE, FINISHED, MEDIA_EXTENSIONS, Job, Status, expand_paths
from ..core.probe import format_duration
from ..core.progress import format_eta
from .preset_dialog import PresetDialog
from .runner import Runner, open_path, reveal
from .workers import Workers

COL_FILE, COL_PRESET, COL_STATUS = range(3)
CHOOSE_FOLDER = "__choose__"
STATUS_ICON = {Status.PROBING: "view-refresh", Status.READY: "", Status.INVALID: "dialog-warning",
               Status.RUNNING: "media-playback-start", Status.PAUSED: "media-playback-pause",
               Status.DONE: "dialog-ok-apply", Status.FAILED: "dialog-error",
               Status.CANCELLED: "dialog-cancel"}


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024


def icon(name, fallback="video-x-generic"):
    return QIcon.fromTheme(name, QIcon.fromTheme(fallback))


class MainWindow(QMainWindow):
    def __init__(self, files=(), settings=None, config_dir=None):
        super().__init__()
        self.setWindowTitle("Konvi")
        self.setWindowIcon(QIcon.fromTheme("konvi", icon("video-x-generic")))
        self.setAcceptDrops(True)
        self.settings = settings or QSettings("konvi", "konvi")
        self.config_dir = Path(config_dir or Path(QStandardPaths.writableLocation(
            QStandardPaths.GenericConfigLocation)) / "konvi")
        self.cache_dir = Path(QStandardPaths.writableLocation(QStandardPaths.GenericCacheLocation)) / "konvi"
        self.custom = presets.load_custom(self.config_dir / "presets.json")
        self.jobs = []
        self.items = {}
        self.hardware = None
        self._syncing = False
        self._batch = set()             # ids de la tanda en curso (para el progreso total)
        self._batch_message = ""        # «5 vídeos convertidos», hasta que cambie la lista

        self.workers = Workers(self)
        self.workers.probed.connect(self._on_probed)
        self.workers.thumbnail.connect(self._on_thumbnail)
        self.workers.hardware_ready.connect(self._on_hardware)
        self.runner = Runner(self)
        self.runner.changed.connect(self._on_job_changed)
        self.runner.batch_finished.connect(self._on_batch_finished)

        self._build_ui()
        preset = self.all_presets().get(self.settings.value("preset", presets.DEFAULT_ID))
        self.default_preset = preset or presets.builtin_by_id()[presets.DEFAULT_ID]
        self._show_preset(self.default_preset)
        self._refresh()
        self.resize(self.settings.value("size", QSize(1060, 700)))
        self.workers.detect_hardware(self.cache_dir / "encoders.json")
        if files:
            self.add_files(files)

    # ------------------------------------------------------------------ interfaz

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(10)

        # destinos
        bar = QHBoxLayout()
        bar.setSpacing(4)
        self.category_group = QButtonGroup(self)
        self.category_buttons = {}
        for cat in presets.CATEGORIES:
            b = QToolButton()
            b.setText(cat.label)
            b.setIcon(icon(cat.icon))
            b.setIconSize(QSize(40, 40))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setCheckable(True)
            b.setAutoRaise(True)
            b.setMinimumWidth(92)
            b.setMinimumHeight(76)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)   # entre todos llenan el ancho
            b.setToolTip(cat.tooltip)
            b.clicked.connect(lambda _=False, c=cat.id: self._category_clicked(c))
            self.category_group.addButton(b)
            self.category_buttons[cat.id] = b
            bar.addWidget(b)
        root.addLayout(bar)

        # perfil
        row = QHBoxLayout()
        self.preset_label = QLabel("Perfil:")
        self.preset_label.setToolTip("El perfil decide cómo se convierte: formato, calidad, tamaño y sonido.\n"
                                     "Se aplica a todos los vídeos de la lista, o solo a los que selecciones.")
        row.addWidget(self.preset_label)
        self.preset_combo = QComboBox()
        self.preset_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.preset_combo.setMinimumWidth(300)
        self.preset_combo.setToolTip("Elige cómo convertir. Al pasar el ratón por cada opción\n"
                                     "de la lista verás para qué sirve.")
        self.preset_combo.activated.connect(self._preset_activated)
        row.addWidget(self.preset_combo, 1)
        self.btn_settings = QPushButton(icon("configure"), "Personalizar…")
        self.btn_settings.setToolTip("Cambiar la calidad, el tamaño final, la resolución o el sonido\n"
                                     "de este perfil y guardarlo en «Mis perfiles» para la próxima vez.")
        self.btn_settings.clicked.connect(self._edit_preset)
        row.addWidget(self.btn_settings)
        self.btn_delete_preset = QToolButton()
        self.btn_delete_preset.setIcon(icon("edit-delete"))
        self.btn_delete_preset.setToolTip("Borrar este perfil de «Mis perfiles»")
        self.btn_delete_preset.setAccessibleName("Borrar perfil")
        self.btn_delete_preset.clicked.connect(self._delete_preset)
        row.addWidget(self.btn_delete_preset)
        root.addLayout(row)

        self.description = QLabel()
        self.description.setWordWrap(True)
        self.description.setToolTip("Para qué sirve el perfil elegido.")
        root.addWidget(self.description)

        self.hw_check = QCheckBox("Más rápido con la tarjeta gráfica")
        self.hw_check.setEnabled(False)
        self.hw_check.setToolTip("Comprobando qué puede hacer la tarjeta gráfica…")
        self.hw_check.toggled.connect(self._hw_toggled)
        root.addWidget(self.hw_check)

        # acciones de la lista
        tools = QHBoxLayout()
        self.btn_add = QPushButton(icon("list-add"), "Añadir vídeos…")
        self.btn_add.setToolTip("Elegir vídeos o audios para convertir (Ctrl+O).\n"
                                "También puedes arrastrarlos a la ventana, o una carpeta entera.")
        self.btn_add.clicked.connect(self._ask_files)
        self.btn_remove = QPushButton(icon("list-remove"), "Quitar")
        self.btn_remove.setToolTip("Quitar de la lista los vídeos seleccionados (Supr).\n"
                                   "No borra nada del disco.")
        self.btn_remove.clicked.connect(self.remove_selected)
        for b in (self.btn_add, self.btn_remove):
            tools.addWidget(b)
        tools.addStretch(1)
        root.addLayout(tools)

        # lista (o el estado vacío)
        self.stack = QStackedWidget()
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.addStretch(1)
        art = QLabel()
        art.setPixmap(icon("video-x-generic").pixmap(96, 96))
        art.setAlignment(Qt.AlignCenter)
        art.setToolTip("Suelta aquí vídeos, audios o carpetas enteras.")
        title = QLabel("Arrastra aquí tus vídeos")
        title.setToolTip("Suelta aquí vídeos, audios o carpetas enteras.")
        f = title.font()
        f.setPointSizeF(f.pointSizeF() * 1.5)
        title.setFont(f)
        title.setAlignment(Qt.AlignCenter)
        hint = QLabel("También carpetas enteras, o pulsa «Añadir vídeos…»")
        hint.setAlignment(Qt.AlignCenter)
        hint.setEnabled(False)
        hint.setToolTip("De una carpeta se añaden todos los vídeos y audios que haya dentro,\n"
                        "también los de sus subcarpetas.")
        for w in (art, title, hint):
            ev.addWidget(w)
        ev.addStretch(1)
        self.stack.addWidget(empty)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Vídeo", "Perfil", "Estado"])
        head = self.tree.headerItem()
        head.setToolTip(COL_FILE, "El vídeo original y sus datos: tamaño de imagen, fotogramas\n"
                                  "por segundo, formato, sonido y duración.")
        head.setToolTip(COL_PRESET, "Cómo se va a convertir cada vídeo. Para cambiarlo, selecciona\n"
                                    "el vídeo y elige otro perfil arriba.")
        head.setToolTip(COL_STATUS, "Cómo va cada vídeo. Clic derecho para más opciones.")
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setIconSize(QSize(96, 54))
        self.tree.setTextElideMode(Qt.ElideMiddle)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        header = self.tree.header()
        header.setSectionResizeMode(COL_FILE, QHeaderView.Stretch)
        header.setStretchLastSection(False)
        self.tree.setColumnWidth(COL_PRESET, 230)
        self.tree.setColumnWidth(COL_STATUS, 250)
        self.stack.addWidget(self.tree)
        root.addWidget(self.stack, 1)

        # abajo: dónde guardar, progreso y la acción principal
        bottom = QHBoxLayout()
        out_label = QLabel("Guardar en:")
        out_label.setToolTip("La carpeta donde se guardan los vídeos convertidos.")
        bottom.addWidget(out_label)
        self.out_combo = QComboBox()
        self.out_combo.setMinimumWidth(260)
        self.out_combo.setToolTip("Dónde se guardan los vídeos convertidos.\n"
                                  "Nunca se sobrescribe nada: si el nombre existe, se añade (2), (3)…")
        self._fill_out_combo(self.settings.value("out_folder", "") or "")
        self.out_combo.activated.connect(self._out_activated)
        bottom.addWidget(self.out_combo)
        self.btn_open_folder = QPushButton(icon("document-open-folder", "folder"), "Abrir carpeta")
        self.btn_open_folder.setToolTip("Abrir la carpeta con los vídeos que se acaban de convertir.")
        self.btn_open_folder.clicked.connect(self._open_output_folder)
        bottom.addWidget(self.btn_open_folder)
        bottom.addStretch(1)
        self.overall_label = QLabel()
        self.overall_label.setToolTip("Resumen de la lista: cuántos vídeos hay y cuánto duran en total,\n"
                                      "o cómo va la conversión.")
        bottom.addWidget(self.overall_label)
        self.overall_bar = QProgressBar()
        self.overall_bar.setFixedWidth(200)
        self.overall_bar.setRange(0, 1000)
        self.overall_bar.setTextVisible(False)
        self.overall_bar.setToolTip("Progreso de todos los vídeos de esta tanda.")
        self.overall_bar.hide()
        bottom.addWidget(self.overall_bar)
        self.btn_pause = QPushButton(icon("media-playback-pause"), "Pausa")
        self.btn_pause.setToolTip("Pausar la conversión (se puede seguir después)")
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_stop = QPushButton(icon("media-playback-stop"), "Detener")
        self.btn_stop.setToolTip("Parar la conversión. El vídeo que se estaba convirtiendo\n"
                                 "se descarta; los ya terminados se quedan.")
        self.btn_stop.clicked.connect(self._stop)
        self.btn_convert = QPushButton(icon("media-playback-start"), "Convertir")
        self.btn_convert.setDefault(True)
        self.btn_convert.setMinimumHeight(36)
        self.btn_convert.setMinimumWidth(140)
        f = self.btn_convert.font()
        f.setBold(True)
        self.btn_convert.setFont(f)
        self.btn_convert.clicked.connect(self.convert)
        for b in (self.btn_pause, self.btn_stop, self.btn_convert):
            bottom.addWidget(b)
        root.addLayout(bottom)

        for seq, slot in ((QKeySequence.Open, self._ask_files), (QKeySequence.Delete, self.remove_selected),
                          (QKeySequence("Ctrl+Return"), self.convert)):
            act = QAction(self)
            act.setShortcut(seq)
            act.triggered.connect(slot)
            self.addAction(act)

    # ------------------------------------------------------------------ perfiles

    def all_presets(self):
        d = presets.builtin_by_id()
        d.update({p.id: p for p in self.custom})
        return d

    def _presets_in(self, category):
        if category == "custom":
            return list(self.custom)
        return [p for p in presets.BUILTIN if p.category == category]

    def _show_preset(self, preset):
        """Pone la interfaz en este perfil sin aplicarlo a nada."""
        self._syncing = True
        try:
            self.category_buttons[preset.category].setChecked(True)
            self._fill_preset_combo(preset.category)
            i = self.preset_combo.findData(preset.id)
            self.preset_combo.setCurrentIndex(max(i, 0))
            self.shown_preset = preset
            self.description.setText(preset.description.replace("\n", " "))
            self.btn_delete_preset.setVisible(preset.custom)
            self.btn_settings.setEnabled(True)
        finally:
            self._syncing = False

    def _fill_preset_combo(self, category):
        self.preset_combo.clear()
        for p in self._presets_in(category):
            self.preset_combo.addItem(p.name, p.id)
            self.preset_combo.setItemData(self.preset_combo.count() - 1, p.description, Qt.ToolTipRole)
        self.category_buttons["custom"].setVisible(bool(self.custom))

    def _category_clicked(self, category):
        items = self._presets_in(category)
        if items:
            same = self.shown_preset if self.shown_preset.category == category else items[0]
            self._choose(same)

    def _preset_activated(self, index):
        preset = self.all_presets().get(self.preset_combo.itemData(index))
        if preset:
            self._choose(preset)

    def _choose(self, preset):
        """El usuario ha elegido un perfil: se aplica a los seleccionados o a toda la lista."""
        self._batch_message = ""
        self._show_preset(preset)
        targets = self._targets()
        for job in targets:
            job.set_preset(preset)
            self._update_item(job)
        if not self._selected_jobs():
            self.default_preset = preset
            self.settings.setValue("preset", preset.id)
        self._refresh()

    def _selected_jobs(self):
        ids = {it.data(0, Qt.UserRole) for it in self.tree.selectedItems()}
        return [j for j in self.jobs if j.id in ids]

    def _targets(self):
        selected = self._selected_jobs()
        return [j for j in (selected or self.jobs) if j.status in EDITABLE]

    def _edit_preset(self):
        dlg = PresetDialog(self.shown_preset, self)
        if dlg.exec() != PresetDialog.Accepted:
            return
        new_id = self.shown_preset.id if self.shown_preset.custom else presets.new_custom_id(self.custom)
        preset = dlg.result_preset(new_id)
        self.custom = [p for p in self.custom if p.id != new_id] + [preset]
        self._save_custom()
        self._choose(preset)

    def _delete_preset(self):
        p = self.shown_preset
        if not p.custom:
            return
        box = QMessageBox(QMessageBox.Question, "Borrar perfil",
                          f"¿Borrar el perfil «{p.name}» de «Mis perfiles»?\n\n"
                          "Los vídeos de la lista que lo usan no cambian.", parent=self)
        delete = box.addButton("Borrar perfil", QMessageBox.DestructiveRole)
        box.addButton("Cancelar", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not delete:
            return
        self.custom = [c for c in self.custom if c.id != p.id]
        self._save_custom()
        fallback = presets.builtin_by_id()[presets.DEFAULT_ID]
        if self.default_preset.id == p.id:
            self.default_preset = fallback
            self.settings.setValue("preset", fallback.id)
        self._show_preset(self.custom[0] if self.custom else self.default_preset)
        self._refresh()

    def _save_custom(self):
        try:
            presets.save_custom(self.config_dir / "presets.json", self.custom)
        except OSError as e:
            QMessageBox.warning(self, "Konvi", f"No se pudieron guardar tus perfiles:\n{e}")

    # ------------------------------------------------------------------ tarjeta gráfica

    def _on_hardware(self, hw):
        self.hardware = self.runner.hardware = hw
        if hw.any:
            kind = "NVIDIA (NVENC)" if hw.nvenc else "VAAPI"
            self.hw_check.setEnabled(True)
            self.hw_check.setToolTip(
                f"Convierte con la tarjeta gráfica ({kind}): mucho más rápido.\n"
                "A la misma calidad, el fichero sale algo más grande.\n"
                "Solo acelera H.264 y H.265; el resto se hace con el procesador.")
            self.hw_check.setChecked(True)        # siempre de serie; desmarcarla vale para esta sesión
        else:
            self.hw_check.setEnabled(False)
            self.hw_check.setChecked(False)
            self.hw_check.setToolTip("No se ha encontrado una tarjeta gráfica que ffmpeg pueda usar\n"
                                     "para convertir (NVIDIA NVENC o VAAPI). Se usa el procesador.")

    def _hw_toggled(self, on):
        self.runner.use_hardware = on

    # ------------------------------------------------------------------ carpeta de salida

    def _fill_out_combo(self, current):
        self.out_combo.clear()
        self.out_combo.addItem(icon("document-open-folder", "folder"), "Junto al original", "")
        for folder in self._recent_folders()[:5]:
            self.out_combo.addItem(icon("folder"), folder, folder)
        self.out_combo.addItem(icon("folder-new", "folder"), "Elegir carpeta…", CHOOSE_FOLDER)
        i = self.out_combo.findData(current)
        self.out_combo.setCurrentIndex(max(i, 0))
        self.runner.output_folder = current or None

    def _recent_folders(self):
        value = self.settings.value("recent_folders", []) or []
        return [value] if isinstance(value, str) else [f for f in value if f]   # QSettings: 1 = str

    def _out_activated(self, index):
        value = self.out_combo.itemData(index)
        if value == CHOOSE_FOLDER:
            start = self.runner.output_folder or str(Path.home())
            folder = QFileDialog.getExistingDirectory(self, "Guardar los vídeos en", start)
            if not folder:
                self._fill_out_combo(self.runner.output_folder or "")
                return
            recent = [folder] + [f for f in self._recent_folders() if f != folder]
            self.settings.setValue("recent_folders", recent[:5])
            value = folder
        self.settings.setValue("out_folder", value)
        self._fill_out_combo(value)

    # ------------------------------------------------------------------ cola

    def add_files(self, paths):
        self._batch_message = ""
        known = {str(j.source) for j in self.jobs if j.status not in FINISHED}
        added = 0
        for path in expand_paths(paths):
            if str(path) in known:
                continue
            known.add(str(path))
            job = Job(source=path, preset=self.default_preset)
            self.jobs.append(job)
            item = QTreeWidgetItem()
            item.setData(0, Qt.UserRole, job.id)
            item.setIcon(COL_FILE, icon("video-x-generic"))
            self.items[job.id] = item
            self.tree.addTopLevelItem(item)
            self._update_item(job)
            self.workers.probe(job.id, path)
            added += 1
        self._refresh()
        return added

    def _job(self, job_id):
        return next((j for j in self.jobs if j.id == job_id), None)

    def _on_probed(self, job_id, info, error):
        job = self._job(job_id)
        if job is None:
            return
        job.set_probed(info, error)
        if info is not None:
            self.workers.thumbnail_for(job_id, info)
            if info.video is None:
                self.items[job_id].setIcon(COL_FILE, icon("audio-x-generic"))
        self._update_item(job)
        self._refresh()

    def _on_thumbnail(self, job_id, image):
        item = self.items.get(job_id)
        if item is not None:
            self.items[job_id].setIcon(COL_FILE, QIcon(QPixmap.fromImage(image)))

    def _update_item(self, job):
        item = self.items.get(job.id)
        if item is None:
            return
        details = (job.info.summary() if job.info else
                   "Leyendo…" if job.status == Status.PROBING else "")
        item.setText(COL_FILE, f"{job.source.name}\n{details}")
        item.setToolTip(COL_FILE, "\n".join(filter(None, [str(job.source), "",
                                                         details.replace(" · ", "\n")])))
        item.setText(COL_PRESET, job.preset.name)
        item.setToolTip(COL_PRESET, job.preset.description)
        text, tip = self._status_text(job)
        item.setText(COL_STATUS, text)
        item.setToolTip(COL_STATUS, tip or text)
        item.setIcon(COL_STATUS, icon(STATUS_ICON[job.status], "")) if STATUS_ICON[job.status] \
            else item.setIcon(COL_STATUS, QIcon())

        bar = self.tree.itemWidget(item, COL_STATUS)
        if job.status in ACTIVE:
            if bar is None:
                bar = QProgressBar()
                bar.setRange(0, 1000)
                self.tree.setItemWidget(item, COL_STATUS, bar)
            bar.setValue(int(job.fraction * 1000))
            bar.setFormat(text)
            bar.setToolTip(tip or text)
        elif bar is not None:
            self.tree.removeItemWidget(item, COL_STATUS)

    def _status_text(self, job):
        s = job.status
        if s == Status.PROBING:
            return "Leyendo…", ""
        if s == Status.READY:
            taken = [j.output for j in self.jobs if j.output and j is not job]
            where = naming.output_path(job.source, job.preset.container, self.runner.output_folder, taken)
            tip = f"Se guardará como:\n{where}"
            return ("En cola" if self.runner.running else "Listo"), "\n\n".join(filter(None, [tip, job.message]))
        if s == Status.INVALID:
            return job.message.split("\n")[0], job.message
        if s == Status.RUNNING:
            parts = [f"{job.fraction * 100:.0f} %"]
            if job.eta is not None:
                parts.append(f"quedan {format_eta(job.eta)}")
            if job.speed:
                parts.append(f"{job.speed:.1f}×".replace(".", ","))
            tip = "Convirtiendo con la tarjeta gráfica" if job.used_hardware else "Convirtiendo"
            return " · ".join(parts), "\n".join(filter(None, [tip, job.message]))
        if s == Status.PAUSED:
            return f"En pausa · {job.fraction * 100:.0f} %", ""
        if s == Status.DONE:
            tip = f"Guardado en:\n{job.output}\n\nDoble clic para verlo."
            if job.message:
                tip += f"\n\n{job.message}"
            return f"Hecho · {human_size(job.output_size)}", tip
        if s == Status.FAILED:
            return "Error: " + job.message.split("\n")[-1], job.message
        return "Cancelado", "Se detuvo antes de terminar; no se ha guardado nada."

    def _on_job_changed(self, job_id):
        job = self._job(job_id)
        if job:
            if job.status in ACTIVE:
                self._batch.add(job.id)     # también los añadidos con la tanda en marcha
            self._update_item(job)
        self._refresh()

    def remove_selected(self):
        for job in self._selected_jobs():
            if job.status in ACTIVE:
                continue            # el que está en marcha se para con «Detener»
            self._remove(job)
        self._refresh()

    def _clear_finished(self):
        for job in [j for j in self.jobs if j.status == Status.DONE]:
            self._remove(job)
        self._refresh()

    def _remove(self, job):
        item = self.items.pop(job.id, None)
        if item is not None:
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
        self.jobs.remove(job)

    def _ask_files(self):
        start = self.settings.value("last_dir", str(Path.home()))
        exts = " ".join(f"*.{e} *.{e.upper()}" for e in sorted(MEDIA_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(self, "Añadir vídeos", start,
                                                f"Vídeo y audio ({exts});;Todos los ficheros (*)")
        if files:
            self.settings.setValue("last_dir", str(Path(files[0]).parent))
            self.add_files(files)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_files(paths)

    # ------------------------------------------------------------------ selección y menú

    def _selection_changed(self):
        selected = self._selected_jobs()
        presets_used = {j.preset.id for j in selected}
        if len(presets_used) == 1:
            self._show_preset(selected[0].preset)
        elif not selected:
            self._show_preset(self.default_preset)
        self._refresh()

    def _double_clicked(self, item, _column):
        job = self._job(item.data(0, Qt.UserRole))
        if job and job.status == Status.DONE and job.output and job.output.exists():
            open_path(job.output)

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        job = self._job(item.data(0, Qt.UserRole)) if item else None
        menu = QMenu(self)
        if job is None:
            menu.addAction(icon("list-add"), "Añadir vídeos…", self._ask_files)
            clear = menu.addAction(icon("edit-clear-history"), "Quitar los terminados", self._clear_finished)
            clear.setEnabled(any(j.status == Status.DONE for j in self.jobs))
            menu.exec(self.tree.viewport().mapToGlobal(pos))
            return
        if job.status == Status.DONE and job.output:
            menu.addAction(icon("media-playback-start"), "Ver el vídeo convertido",
                           lambda: open_path(job.output))
            menu.addAction(icon("document-open-folder", "folder"), "Mostrar en la carpeta",
                           lambda: reveal(job.output))
        menu.addAction(icon("video-x-generic"), "Ver el original", lambda: open_path(job.source))
        if job.status in FINISHED:
            menu.addAction(icon("view-refresh"), "Volver a convertir", lambda: self._retry(job))
        if job.id in self.runner.logs and job.status == Status.FAILED:
            menu.addAction(icon("dialog-information"), "Ver el error completo",
                           lambda: self._show_log(job))
        menu.addSeparator()
        remove = menu.addAction(icon("list-remove"), "Quitar de la lista", self.remove_selected)
        remove.setEnabled(job.status not in ACTIVE)
        clear = menu.addAction(icon("edit-clear-history"), "Quitar los terminados", self._clear_finished)
        clear.setEnabled(any(j.status == Status.DONE for j in self.jobs))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _open_output_folder(self):
        done = [j for j in self.jobs if j.status == Status.DONE and j.output and j.output.exists()]
        if done:
            reveal(done[-1].output)
        elif self.runner.output_folder:
            open_path(self.runner.output_folder)

    def _retry(self, job):
        job.reset()
        self._update_item(job)
        self._refresh()

    def _show_log(self, job):
        box = QMessageBox(QMessageBox.Warning, "Error al convertir",
                          f"No se pudo convertir «{job.source.name}».\n\n{job.message}", parent=self)
        box.setDetailedText(self.runner.logs.get(job.id, ""))
        box.exec()

    # ------------------------------------------------------------------ convertir

    def convert(self):
        if self.runner.running or not any(j.status == Status.READY for j in self.jobs):
            return
        self._batch = {j.id for j in self.jobs if j.status == Status.READY}
        self._batch_message = ""
        self.runner.start(lambda: self.jobs)
        self._refresh()

    def _toggle_pause(self):
        if self.runner.paused:
            self.runner.resume()
        else:
            self.runner.pause()
        self._refresh()

    def _stop(self):
        self.runner.stop()

    def _on_batch_finished(self, done, failed, stopped):
        for job in self.jobs:
            self._update_item(job)
        self._refresh()
        if stopped and not done:
            return
        if failed:
            msg = f"{done} convertidos, {failed} con errores"
        else:
            msg = "1 vídeo convertido" if done == 1 else f"{done} vídeos convertidos"
        self._batch_message = msg
        self._refresh()
        if not self.isActiveWindow():
            QApplication.alert(self)
            if shutil.which("notify-send"):
                subprocess.Popen(["notify-send", "-a", "Konvi", "-i", "konvi", "Konvi", msg])

    def _refresh(self):
        """Estado de los botones y de la barra de abajo."""
        running = self.runner.running
        has_jobs = bool(self.jobs)
        self.stack.setCurrentIndex(1 if has_jobs else 0)
        ready = sum(1 for j in self.jobs if j.status == Status.READY)
        probing = sum(1 for j in self.jobs if j.status == Status.PROBING)

        self.btn_convert.setVisible(not running)
        self.btn_pause.setVisible(running)
        self.btn_stop.setVisible(running)
        self.overall_bar.setVisible(running)
        self.btn_convert.setEnabled(ready > 0)
        if ready:
            self.btn_convert.setToolTip(f"Convertir {ready} {'vídeo' if ready == 1 else 'vídeos'} (Ctrl+Intro)")
        elif probing:
            self.btn_convert.setToolTip("Espera: aún se están leyendo los ficheros.")
        elif has_jobs:
            self.btn_convert.setToolTip("No hay nada listo para convertir.\n"
                                        "Los terminados se pueden repetir con clic derecho → «Volver a convertir».")
        else:
            self.btn_convert.setToolTip("Añade vídeos para convertir.")
        self.btn_pause.setText("Continuar" if self.runner.paused else "Pausa")
        self.btn_pause.setIcon(icon("media-playback-start" if self.runner.paused else "media-playback-pause"))
        self.btn_remove.setEnabled(any(j.status not in ACTIVE for j in self._selected_jobs()))
        if not self.btn_remove.isEnabled():
            self.btn_remove.setToolTip("Selecciona en la lista los vídeos que quieras quitar.")
        else:
            self.btn_remove.setToolTip("Quitar de la lista los vídeos seleccionados (Supr).\n"
                                       "No borra nada del disco.")
        self.btn_open_folder.setVisible(not running and any(j.status == Status.DONE for j in self.jobs))
        self.out_combo.setEnabled(not running)

        # a quién se aplica el perfil, dicho en la propia etiqueta
        selected = [j for j in self._selected_jobs() if j.status in EDITABLE]
        editable = [j for j in self.jobs if j.status in EDITABLE]
        if selected:
            n = len(selected)
            self.preset_label.setText("Perfil del vídeo seleccionado:" if n == 1 else f"Perfil de los {n} seleccionados:")
        elif len(editable) > 1:
            self.preset_label.setText(f"Perfil para los {len(editable)} vídeos:")
        elif editable:
            self.preset_label.setText("Perfil para el vídeo:")
        else:
            self.preset_label.setText("Perfil:")

        if running:
            batch = [j for j in self.jobs if j.id in self._batch]
            total = sum(max(j.info.duration, 1) for j in batch if j.info) or 1
            done = sum(max(j.info.duration, 1) * (1 if j.status in FINISHED else j.fraction)
                       for j in batch if j.info)
            self.overall_bar.setValue(int(done / total * 1000))
            current = next((j for j in batch if j.status in ACTIVE), None)
            index = sum(1 for j in batch if j.status in FINISHED) + 1
            text = f"{min(index, len(batch))} de {len(batch)}"
            if current and current.speed and current.info:
                left = sum(j.info.duration * (1 - j.fraction) for j in batch
                           if j.info and j.status not in FINISHED)
                text += f" · quedan unos {format_eta(left / current.speed)}"
            self.overall_label.setText(("En pausa · " if self.runner.paused else "Convirtiendo ") + text)
        elif self._batch_message:
            self.overall_label.setText(self._batch_message)
        elif ready:
            total = sum(j.info.duration for j in self.jobs if j.info and j.status == Status.READY)
            self.overall_label.setText(f"{ready} {'vídeo' if ready == 1 else 'vídeos'} · {format_duration(total)} en total")
        else:
            self.overall_label.setText("")

    # ------------------------------------------------------------------ cerrar

    def closeEvent(self, event):
        if self.runner.running:
            box = QMessageBox(QMessageBox.Question, "Konvi",
                              "Hay una conversión en marcha. Si sales, el vídeo que se está "
                              "convirtiendo se descarta.", parent=self)
            leave = box.addButton("Detener y salir", QMessageBox.DestructiveRole)
            box.addButton("Seguir convirtiendo", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is not leave:
                event.ignore()
                return
            self.runner.stop()
            self.runner.wait()
        if not self.isMaximized():
            self.settings.setValue("size", self.size())
        super().closeEvent(event)
