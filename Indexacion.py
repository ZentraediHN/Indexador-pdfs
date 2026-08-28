import sys
import os
import fitz  # PyMuPDF
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QRadioButton, QButtonGroup,
    QFileDialog, QListWidget, QProgressBar, QTextEdit, QGroupBox,
    QSpinBox, QMessageBox, QCheckBox
)
from PySide6.QtCore import Qt, QThread, Signal

# --- FUNCIONES DE PROCESAMIENTO DE PDF ---

def insertar_texto_orientado(pagina, punto_visual, texto, fontsize, color, fontname="helv"):
    """
    Inserta texto usando coordenadas 'visuales' respetando la orientación real de la página.
    """
    rotacion = pagina.rotation
    if rotacion == 0:
        pagina.insert_text(punto_visual, texto, fontsize=fontsize, color=color, fontname=fontname)
    else:
        punto_contenido = punto_visual * pagina.derotation_matrix
        pagina.insert_text(punto_contenido, texto, fontsize=fontsize, color=color,
                           fontname=fontname, rotate=rotacion)

def ancho_texto_visual(texto, fontname, fontsize):
    return fitz.get_text_length(texto, fontname, fontsize)

# --- HILO DE TRABAJO EN SEGUNDO PLANO ---

class WorkerThread(QThread):
    progress = Signal(int)
    log = Signal(str)
    finished = Signal()

    def __init__(self, pdf_paths, output_dir, enable_left, left_text, 
                 enable_right, right_mode, delimiter, block_idx, custom_text, 
                 enable_page_nums):
        super().__init__()
        self.pdf_paths = pdf_paths
        self.output_dir = output_dir
        self.enable_left = enable_left
        self.left_text = left_text
        self.enable_right = enable_right
        self.right_mode = right_mode  # 'delimiter' o 'custom'
        self.delimiter = delimiter
        self.block_idx = block_idx
        self.custom_text = custom_text
        self.enable_page_nums = enable_page_nums

    def run(self):
        FONTSIZE = 20
        color_rojo = (1, 0, 0)
        total_archivos = len(self.pdf_paths)

        for idx, pdf_path in enumerate(self.pdf_paths):
            nombre_archivo = os.path.basename(pdf_path)
            self.log.emit(f"\n📄 Procesando: {nombre_archivo}")

            try:
                pdf_document = fitz.open(pdf_path)
                total_paginas = len(pdf_document)
                nombre_sin_ext = os.path.splitext(nombre_archivo)[0]

                for num_pag in range(total_paginas):
                    pagina = pdf_document[num_pag]
                    ancho_pagina = pagina.rect.width
                    y_posicion = 0.80 * 28.35
                    margen_lateral = 1.8 * 28.35

                    # --- 1. LEYENDA IZQUIERDA (OPCIONAL) ---
                    if self.enable_left and self.left_text.strip():
                        punto_izq = fitz.Point(margen_lateral, y_posicion)
                        insertar_texto_orientado(pagina, punto_izq, self.left_text.strip(), FONTSIZE, color_rojo)

                    # --- 2. LEYENDA DERECHA Y NUMERACIÓN (OPCIONALES) ---
                    partes_derecha = []

                    # Texto de leyenda derecha (si está activo)
                    if self.enable_right:
                        if self.right_mode == 'delimiter':
                            if self.delimiter in nombre_sin_ext:
                                bloques = nombre_sin_ext.split(self.delimiter)
                                if 0 <= self.block_idx < len(bloques):
                                    partes_derecha.append(bloques[self.block_idx].strip())
                                else:
                                    partes_derecha.append(bloques[0].strip())
                            else:
                                partes_derecha.append(nombre_sin_ext)
                        elif self.right_mode == 'custom' and self.custom_text.strip():
                            partes_derecha.append(self.custom_text.strip())

                    # Numeración de páginas (si está activa)
                    if self.enable_page_nums:
                        partes_derecha.append(f"{num_pag + 1}/{total_paginas}")

                    # Si hay algo que imprimir en la derecha, lo estampamos
                    texto_derecho = " ".join(partes_derecha).strip()
                    if texto_derecho:
                        ancho_txt = ancho_texto_visual(texto_derecho, "helv", FONTSIZE)
                        x_derecho = ancho_pagina - margen_lateral - ancho_txt
                        punto_der = fitz.Point(x_derecho, y_posicion)
                        insertar_texto_orientado(pagina, punto_der, texto_derecho, FONTSIZE, color_rojo)

                # Guardar el resultado
                ruta_salida = os.path.join(self.output_dir, f"procesado_{nombre_archivo}")
                pdf_document.save(ruta_salida)
                pdf_document.close()

                self.log.emit(f"  ✅ Guardado: {ruta_salida}")

            except Exception as e:
                self.log.emit(f"  ❌ Error: {str(e)}")

            porcentaje = int(((idx + 1) / total_archivos) * 100)
            self.progress.emit(porcentaje)

        self.finished.emit()

# --- INTERFAZ GRÁFICA PRINCIPAL ---

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Configurador de Leyendas PDF")
        self.resize(720, 700)
        self.pdf_files = []
        self.init_ui()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- SECCIÓN 1: SELECCIÓN DE ARCHIVOS Y DESTINO ---
        group_files = QGroupBox("1. Archivos PDF y Carpeta de Destino")
        layout_files = QVBoxLayout()

        btn_select_files = QPushButton("📁 Seleccionar Archivos PDF")
        btn_select_files.clicked.connect(self.select_files)
        self.list_files = QListWidget()
        self.list_files.setMaximumHeight(80)

        layout_folder = QHBoxLayout()
        self.input_output_dir = QLineEdit()
        self.input_output_dir.setPlaceholderText("Carpeta de salida para los archivos procesados...")
        btn_select_folder = QPushButton("Examinar...")
        btn_select_folder.clicked.connect(self.select_output_folder)
        layout_folder.addWidget(self.input_output_dir)
        layout_folder.addWidget(btn_select_folder)

        layout_files.addWidget(btn_select_files)
        layout_files.addWidget(self.list_files)
        layout_files.addLayout(layout_folder)
        group_files.setLayout(layout_files)
        main_layout.addWidget(group_files)

        # --- SECCIÓN 2: LEYENDA IZQUIERDA ---
        group_left = QGroupBox("2. Leyenda Izquierda")
        layout_left = QVBoxLayout()

        self.chk_enable_left = QCheckBox("Poner Leyenda Izquierda")
        self.chk_enable_left.setChecked(True)
        self.chk_enable_left.toggled.connect(self.toggle_left_options)

        self.layout_left_input = QHBoxLayout()
        self.layout_left_input.setContentsMargins(20, 0, 0, 0)
        self.lbl_left = QLabel("Texto de la leyenda:")
        self.input_left_text = QLineEdit("DPOT")
        self.layout_left_input.addWidget(self.lbl_left)
        self.layout_left_input.addWidget(self.input_left_text)

        layout_left.addWidget(self.chk_enable_left)
        layout_left.addLayout(self.layout_left_input)
        group_left.setLayout(layout_left)
        main_layout.addWidget(group_left)

        # --- SECCIÓN 3: LEYENDA DERECHA Y NUMERACIÓN ---
        group_right = QGroupBox("3. Leyenda Derecha y Numeración de Páginas")
        layout_right = QVBoxLayout()

        # Checkbox activar leyenda derecha
        self.chk_enable_right = QCheckBox("Poner Leyenda Derecha (Nombre / Texto)")
        self.chk_enable_right.setChecked(True)
        self.chk_enable_right.toggled.connect(self.toggle_right_options)

        # Sub-opciones leyenda derecha
        self.widget_right_options = QWidget()
        layout_right_sub = QVBoxLayout(self.widget_right_options)
        layout_right_sub.setContentsMargins(20, 0, 0, 0)

        self.group_bg = QButtonGroup(self)

        # Opción A: Delimitador
        self.radio_delim = QRadioButton("Tomar parte del nombre del archivo por delimitador")
        self.radio_delim.setChecked(True)
        self.group_bg.addButton(self.radio_delim)

        layout_delim_opts = QHBoxLayout()
        layout_delim_opts.setContentsMargins(20, 0, 0, 0)
        layout_delim_opts.addWidget(QLabel("Delimitador:"))
        self.input_delimiter = QLineEdit(" ")
        self.input_delimiter.setMaximumWidth(50)
        layout_delim_opts.addWidget(self.input_delimiter)

        layout_delim_opts.addWidget(QLabel("Bloque a tomar:"))
        self.spin_block = QSpinBox()
        self.spin_block.setMinimum(0)
        self.spin_block.setValue(0)
        layout_delim_opts.addWidget(self.spin_block)
        layout_delim_opts.addStretch()

        # Opción B: Texto Personalizado
        self.radio_custom = QRadioButton("Usar texto libre personalizado")
        self.group_bg.addButton(self.radio_custom)

        layout_custom_opts = QHBoxLayout()
        layout_custom_opts.setContentsMargins(20, 0, 0, 0)
        self.input_custom_text = QLineEdit()
        self.input_custom_text.setPlaceholderText("Escribe la leyenda derecha aquí...")
        layout_custom_opts.addWidget(self.input_custom_text)

        layout_right_sub.addWidget(self.radio_delim)
        layout_right_sub.addLayout(layout_delim_opts)
        layout_right_sub.addWidget(self.radio_custom)
        layout_right_sub.addLayout(layout_custom_opts)

        # Checkbox Numeración de Página
        self.chk_page_nums = QCheckBox("Incluir Numeración de Páginas (ej. '1/10')")
        self.chk_page_nums.setChecked(True)

        layout_right.addWidget(self.chk_enable_right)
        layout_right.addWidget(self.widget_right_options)
        layout_right.addWidget(self.chk_page_nums)
        group_right.setLayout(layout_right)
        main_layout.addWidget(group_right)

        # --- SECCIÓN 4: BOTÓN Y REGISTRO ---
        self.btn_process = QPushButton("🚀 Procesar PDFs")
        self.btn_process.setStyleSheet("font-weight: bold; padding: 10px; font-size: 14px;")
        self.btn_process.clicked.connect(self.start_processing)
        main_layout.addWidget(self.btn_process)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        main_layout.addWidget(self.progress_bar)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        main_layout.addWidget(self.log_output)

    # --- CONTROL DE INTERFAZ (Habilitar / Deshabilitar elementos) ---

    def toggle_left_options(self, checked):
        self.lbl_left.setEnabled(checked)
        self.input_left_text.setEnabled(checked)

    def toggle_right_options(self, checked):
        self.widget_right_options.setEnabled(checked)

    def select_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Seleccionar PDFs", "", "Archivos PDF (*.pdf)")
        if files:
            self.pdf_files = files
            self.list_files.clear()
            for f in files:
                self.list_files.addItem(os.path.basename(f))

    def select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar Carpeta de Salida")
        if folder:
            self.input_output_dir.setText(folder)

    # --- INICIO DEL PROCESAMIENTO ---

    def start_processing(self):
        if not self.pdf_files:
            QMessageBox.warning(self, "Atención", "Selecciona al menos un archivo PDF.")
            return

        output_dir = self.input_output_dir.text().strip()
        if not output_dir or not os.path.exists(output_dir):
            QMessageBox.warning(self, "Atención", "Selecciona una carpeta de salida válida.")
            return

        right_mode = 'delimiter' if self.radio_delim.isChecked() else 'custom'

        self.btn_process.setEnabled(False)
        self.progress_bar.setValue(0)
        self.log_output.clear()

        # Iniciar Worker en hilo secundario
        self.worker = WorkerThread(
            pdf_paths=self.pdf_files,
            output_dir=output_dir,
            enable_left=self.chk_enable_left.isChecked(),
            left_text=self.input_left_text.text(),
            enable_right=self.chk_enable_right.isChecked(),
            right_mode=right_mode,
            delimiter=self.input_delimiter.text(),
            block_idx=self.spin_block.value(),
            custom_text=self.input_custom_text.text(),
            enable_page_nums=self.chk_page_nums.isChecked()
        )

        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.log.connect(self.log_output.append)
        self.worker.finished.connect(self.on_processing_finished)
        self.worker.start()

    def on_processing_finished(self):
        self.btn_process.setEnabled(True)
        QMessageBox.information(self, "Éxito", "¡Procesamiento completado correctamente!")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())