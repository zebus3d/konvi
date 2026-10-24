"""«Personalizar…»: cambiar un perfil y guardarlo en «Mis perfiles»."""

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFormLayout, QGridLayout, QLabel, QLineEdit,
                               QRadioButton, QSlider, QVBoxLayout, QWidget)

from ..core.command import LOSSY_AUDIO
from ..core.presets import QUALITY_NAMES, QUALITY_STEPS
from ..i18n import tr

RESOLUTIONS = (("Como el original", None), ("4K · 2160p", 2160), ("1440p", 1440),
               ("1080p (Full HD)", 1080), ("720p (HD)", 720), ("480p", 480), ("360p", 360))
FPS = (("Como el original", None), ("60", 60.0), ("50", 50.0), ("30", 30.0), ("25", 25.0),
       ("24", 24.0))
# al sacar imágenes, además: pocas por segundo (capturas)
IMAGE_FPS = (("Una imagen por segundo", 1.0), ("Una imagen cada 10 segundos", 0.1))
SPEEDS = (("Rápida (el fichero sale algo más grande)", "fast"), ("Equilibrada", "medium"),
          ("Lenta (el fichero sale más pequeño)", "slow"))
AUDIO_KBPS = (96, 128, 160, 192, 256, 320, 384)
TARGET_CODECS = {"h264", "h265", "av1", "vp9"}


def _row(form, text, widget):
    """Fila del formulario cuya etiqueta explica lo mismo que su control."""
    label = QLabel(text)
    label.setToolTip(widget.toolTip())
    label.setBuddy(widget)
    form.addRow(label, widget)


def _select(combo, value):
    i = combo.findData(value)
    combo.setCurrentIndex(i if i >= 0 else 0)


class PresetDialog(QDialog):
    def __init__(self, preset, parent=None):
        super().__init__(parent)
        self.preset = preset
        self.setWindowTitle(tr("Personalizar el perfil"))
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        layout.addLayout(form)

        self.name = QLineEdit(preset.name if preset.custom else tr("{name} (mío)", name=tr(preset.name)))
        self.name.setToolTip(tr("Con este nombre aparecerá en «Mis perfiles»."))
        _row(form, tr("Nombre:"), self.name)

        video = preset.video
        steps = QUALITY_STEPS.get(video)
        variable_quality = bool(steps) and not preset.lossless

        # ---- calidad o tamaño final
        quality_box = QWidget()
        grid = QGridLayout(quality_box)
        grid.setContentsMargins(0, 0, 0, 0)
        self.by_quality = QRadioButton(tr("Calidad:"))
        self.by_size = QRadioButton(tr("Tamaño final:"))
        group = QButtonGroup(self)
        group.addButton(self.by_quality)
        group.addButton(self.by_size)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, len(QUALITY_NAMES) - 1)
        self.slider.setTickPosition(QSlider.TicksBelow)
        self.slider.setPageStep(1)
        self.quality_label = QLabel()
        self.quality_label.setMinimumWidth(70)
        self.slider.valueChanged.connect(self._quality_changed)
        self.slider.setToolTip(tr("A la izquierda, ficheros más pequeños; a la derecha, más calidad.\n"
                                  "«Alta» se ve igual que el original en casi todo."))
        small, big = QLabel(tr("Más pequeño")), QLabel(tr("Mejor calidad"))
        small.setToolTip(tr("Hacia aquí: ficheros más pequeños, con menos calidad."))
        big.setToolTip(tr("Hacia aquí: más calidad, ficheros más grandes."))
        self.by_quality.setToolTip(tr("Calidad constante: cada vídeo ocupa lo que necesite para verse así.\n"
                                      "Es lo recomendado."))
        self.by_size.setToolTip(tr("Elegir cuánto ocupará el fichero: la calidad se ajusta para caber.\n"
                                   "Útil para enviarlo por correo o a una web con límite de tamaño."))
        self.quality_label.setToolTip(tr("La calidad elegida."))
        for w in (small, big):
            w.setEnabled(False)       # texto atenuado: es una pista, no un control
        self.size_mb = QDoubleSpinBox()
        self.size_mb.setRange(1, 100000)
        self.size_mb.setDecimals(1)
        self.size_mb.setSuffix(" MB")
        self.size_mb.setValue(preset.target_mb or 25)
        self.size_mb.setToolTip(tr("El vídeo se ajusta para no pasar de este tamaño (con el audio).\n"
                                   "Cuanto más largo sea el vídeo, peor se verá."))
        grid.addWidget(self.by_quality, 0, 0)
        grid.addWidget(small, 0, 1)
        grid.addWidget(self.slider, 0, 2)
        grid.addWidget(big, 0, 3)
        grid.addWidget(self.quality_label, 0, 4)
        grid.addWidget(self.by_size, 1, 0)
        grid.addWidget(self.size_mb, 1, 1, 1, 2)
        grid.setColumnStretch(2, 1)
        form.addRow(quality_box)

        q = preset.effective_quality()
        if variable_quality:
            idx = min(range(len(steps)), key=lambda i: abs(steps[i] - q))
            self.slider.setValue(len(steps) - 1 - idx)
        else:
            self.slider.setValue(len(QUALITY_NAMES) - 1)
        self._quality_changed(self.slider.value())
        (self.by_size if preset.target_mb else self.by_quality).setChecked(True)
        self.by_quality.toggled.connect(self._sync)
        if not variable_quality:
            why = (tr("Es sin pérdida: la calidad es siempre la del original.")
                   if preset.lossless or video in ("ffv1", "png", "tiff")
                   else tr("Este formato tiene calidad fija.") if video else "")
            for w in (self.by_quality, self.slider, self.by_size, self.size_mb):
                w.setEnabled(False)
                w.setToolTip(why)
            if not video:
                quality_box.hide()
        elif video not in TARGET_CODECS:
            self.by_size.setEnabled(False)
            self.size_mb.setEnabled(False)
            self.by_size.setToolTip(tr("Este formato no admite ajustar a un tamaño."))

        # ---- imagen
        self.resolution = QComboBox()
        for text, value in RESOLUTIONS:
            self.resolution.addItem(tr(text), value)
        _select(self.resolution, int(preset.max_height) if preset.max_height else None)
        self.resolution.setToolTip(tr("Solo reduce: si el original es más pequeño, se deja como está.\n"
                                      "En vídeos verticales se refiere al lado corto."))
        self.fps = QComboBox()
        for text, value in FPS + (IMAGE_FPS if preset.is_image_sequence else ()):
            self.fps.addItem(tr(text), value)
        _select(self.fps, float(preset.max_fps) if preset.max_fps else None)
        self.fps.setToolTip(tr("Máximo de fotogramas por segundo. Si el original tiene menos,\n"
                               "se deja como está."))
        self.speed = QComboBox()
        for text, value in SPEEDS:
            self.speed.addItem(tr(text), value)
        _select(self.speed, preset.speed)
        self.speed.setToolTip(tr("Cuánto se esfuerza el compresor. La calidad es la misma;\n"
                                 "lo que cambia es el tamaño del fichero y lo que tarda."))
        if video:
            _row(form, tr("Resolución máxima:"), self.resolution)
            _row(form, tr("Fotogramas por segundo:"), self.fps)
            if video in TARGET_CODECS and not preset.lossless:
                _row(form, tr("Velocidad:"), self.speed)
            if preset.profile == "dvd":
                for w in (self.resolution, self.fps):
                    w.setEnabled(False)
                    w.setToolTip(tr("El DVD tiene siempre 720×576 a 25 fps."))

        # ---- audio
        self.audio_kbps = QComboBox()
        for k in AUDIO_KBPS:
            self.audio_kbps.addItem(f"{k} kbps", k)
        _select(self.audio_kbps, preset.audio_kbps)
        self.audio_kbps.setToolTip(tr("Calidad del sonido. 192 kbps está bien para casi todo;\n"
                                      "320 si es música."))
        self.stereo = QCheckBox(tr("Pasar a estéreo"))
        self.stereo.setChecked(preset.stereo)
        self.stereo.setToolTip(tr("Mezcla el sonido envolvente (5.1, 7.1) a dos canales.\n"
                                  "Recomendado para móviles, auriculares y redes sociales."))
        if preset.audio in LOSSY_AUDIO:
            _row(form, tr("Sonido:"), self.audio_kbps)
        if preset.audio and preset.audio != "copy" and not preset.surround:
            form.addRow("", self.stereo)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        save = buttons.button(QDialogButtonBox.Save)
        save.setText(tr("Guardar en Mis perfiles"))
        save.setToolTip(tr("Guarda estos ajustes como un perfil nuevo en «Mis perfiles»\n"
                           "y lo aplica a los vídeos de la lista."))
        buttons.button(QDialogButtonBox.Cancel).setText(tr("Cancelar"))
        buttons.button(QDialogButtonBox.Cancel).setToolTip(tr("Cerrar sin cambiar nada."))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._sync()

    def _quality_changed(self, value):
        self.quality_label.setText(tr(QUALITY_NAMES[len(QUALITY_NAMES) - 1 - value]))

    def _sync(self, *_):
        if self.by_quality.isEnabled():
            self.slider.setEnabled(self.by_quality.isChecked())
            self.size_mb.setEnabled(self.by_size.isChecked() and self.by_size.isEnabled())

    def _accept(self):
        if not self.name.text().strip():
            self.name.setFocus()
            self.name.setPlaceholderText(tr("Escribe un nombre"))
            return
        self.accept()

    def result_preset(self, new_id):
        p = self.preset
        steps = QUALITY_STEPS.get(p.video)
        quality, target = p.quality, None
        if steps and not p.lossless:
            quality = steps[len(steps) - 1 - self.slider.value()]
            if self.by_size.isChecked() and self.by_size.isEnabled():
                target = self.size_mb.value()
        return replace(
            p, id=new_id, category="custom", custom=True, name=self.name.text().strip(),
            description=tr(p.description),
            quality=quality, target_mb=target,
            max_height=self.resolution.currentData() if p.video and p.profile != "dvd" else p.max_height,
            max_fps=self.fps.currentData() if p.video and p.profile != "dvd" else p.max_fps,
            speed=self.speed.currentData() if p.video in TARGET_CODECS else p.speed,
            audio_kbps=self.audio_kbps.currentData() if p.audio in LOSSY_AUDIO else p.audio_kbps,
            stereo=self.stereo.isChecked() if p.audio else p.stereo)
