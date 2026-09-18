#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import fitz  # PyMuPDF
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QRadioButton, QButtonGroup,
    QFileDialog, QListWidget, QProgressBar, QTextEdit, QGroupBox,
    QSpinBox, QMessageBox, QCheckBox, QMenuBar, QMenu, QComboBox,
    QScrollArea
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction

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

def ruta_dentro_de(ruta, carpeta):
    """True si 'ruta' está dentro de 'carpeta' (seguro entre unidades distintas)."""
    try:
        ruta = os.path.normcase(os.path.abspath(ruta))
        carpeta = os.path.normcase(os.path.abspath(carpeta))
        return os.path.commonpath([ruta, carpeta]) == carpeta
    except ValueError:
        return False

def formatear_duracion(segundos):
    """Convierte segundos a un texto legible: '3.42 s' o '2 min 5.10 s'."""
    if segundos < 60:
        return f"{segundos:.2f} s"
    minutos = int(segundos // 60)
    resto = segundos - (minutos * 60)
    return f"{minutos} min {resto:.2f} s"

# --- HILO DE TRABAJO EN SEGUNDO PLANO ---

# Constantes de posicionamiento: no dependen de cada página ni de cada
# archivo, así que se calculan una sola vez a nivel de módulo en vez de
# recalcularse en cada iteración del bucle de páginas.
FONTSIZE = 20
COLOR_ROJO = (1, 0, 0)
MARGEN_LATERAL = 1.8 * 28.35
Y_POSICION_TOP = 0.80 * 28.35
MAX_HILOS_SIMULTANEOS = 4  # tope razonable; no tiene sentido pasar del nº de núcleos


class WorkerThread(QThread):
    progress = Signal(int)
    log = Signal(str)
    # OJO: QThread ya trae una señal nativa llamada "finished" que Qt emite
    # automáticamente cuando run() termina. Si se declara aquí OTRA señal
    # con ese mismo nombre, ambas terminan disparando el mismo slot conectado
    # y el diálogo final aparece dos veces. Por eso esta señal propia se
    # llama distinto: proceso_terminado.
    proceso_terminado = Signal(float, int)

    def __init__(self, pdf_items, output_dir, enable_left, left_text, 
                 enable_bottom_left, bottom_left_text,
                 enable_right, right_mode, delimiter, block_idx, custom_text, 
                 enable_extra_page_nums, extra_page_num_pos, extra_page_num_format):
        super().__init__()
        # pdf_items: lista de tuplas (ruta_absoluta_origen, ruta_relativa_destino)
        self.pdf_items = pdf_items
        self.output_dir = output_dir
        self.enable_left = enable_left
        self.left_text = left_text
        self.enable_bottom_left = enable_bottom_left
        self.bottom_left_text = bottom_left_text
        self.enable_right = enable_right
        self.right_mode = right_mode
        self.delimiter = delimiter
        self.block_idx = block_idx
        self.custom_text = custom_text
        # Numeración ADICIONAL (abajo). La numeración de la indexación va
        # siempre dentro de la leyenda superior derecha con formato fijo
        # "num/total"; esta otra sí admite distintos formatos.
        self.enable_extra_page_nums = enable_extra_page_nums
        self.extra_page_num_pos = extra_page_num_pos
        # Plantilla de texto con los marcadores {pagina} y {total}, p. ej.
        # "Página {pagina} de {total}".
        self.extra_page_num_format = extra_page_num_format

        # Contador y candado para reportar progreso de forma segura desde
        # varios hilos trabajadores a la vez.
        self._completados = 0
        self._lock = threading.Lock()

    def _formatear_numeracion_extra(self, pagina, total):
        """Aplica la plantilla elegida por el usuario, p. ej.
        'Página {pagina} de {total}'. Si la plantilla personalizada tiene
        un error de escritura (marcador mal escrito, llave sin cerrar),
        se cae de forma segura al formato clásico 'num/total' en vez de
        interrumpir todo el procesamiento del archivo."""
        try:
            return self.extra_page_num_format.format(pagina=pagina, total=total)
        except (KeyError, IndexError, ValueError):
            return f"{pagina}/{total}"

    def _procesar_un_archivo(self, item):
        """Procesa un único PDF de principio a fin. Se ejecuta en un hilo
        del pool; no debe tocar directamente widgets de la interfaz, solo
        emitir señales (eso sí es seguro entre hilos en Qt)."""
        pdf_path, rel_path = item
        nombre_archivo = os.path.basename(pdf_path)

        try:
            pdf_document = fitz.open(pdf_path)
            total_paginas = len(pdf_document)
            nombre_sin_ext = os.path.splitext(nombre_archivo)[0]
            paginas_procesadas = 0

            for num_pag in range(total_paginas):
                pagina = pdf_document[num_pag]
                ancho_pagina = pagina.rect.width
                alto_pagina = pagina.rect.height

                # Solo esta depende de la página actual (el alto puede
                # variar de una página a otra dentro del mismo documento).
                y_posicion_bottom = alto_pagina - (0.80 * 28.35)

                # --- 1. LEYENDA SUPERIOR IZQUIERDA (OPCIONAL) ---
                if self.enable_left and self.left_text.strip():
                    punto_izq_top = fitz.Point(MARGEN_LATERAL, Y_POSICION_TOP)
                    insertar_texto_orientado(pagina, punto_izq_top, self.left_text.strip(), FONTSIZE, COLOR_ROJO)

                # --- 2. LEYENDA INFERIOR IZQUIERDA (OPCIONAL) ---
                if self.enable_bottom_left and self.bottom_left_text.strip():
                    punto_izq_bottom = fitz.Point(MARGEN_LATERAL, y_posicion_bottom)
                    insertar_texto_orientado(pagina, punto_izq_bottom, self.bottom_left_text.strip(), FONTSIZE, COLOR_ROJO)

                # --- 3. LEYENDA SUPERIOR DERECHA ---
                partes_derecha = []

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

                    # La numeración forma parte de la indexación: siempre
                    # se adjunta al final de la leyenda superior derecha.
                    partes_derecha.append(f"{num_pag + 1}/{total_paginas}")

                texto_derecho = " ".join(partes_derecha).strip()
                if texto_derecho:
                    ancho_txt = ancho_texto_visual(texto_derecho, "helv", FONTSIZE)
                    x_derecho = ancho_pagina - MARGEN_LATERAL - ancho_txt
                    punto_der = fitz.Point(x_derecho, Y_POSICION_TOP)
                    insertar_texto_orientado(pagina, punto_der, texto_derecho, FONTSIZE, COLOR_ROJO)

                # --- 4. NUMERACIÓN ADICIONAL (SOLO ABAJO) ---
                if self.enable_extra_page_nums and self.extra_page_num_pos in ('bottom_right', 'bottom_center'):
                    txt_num_pag = self._formatear_numeracion_extra(num_pag + 1, total_paginas)
                    ancho_num_txt = ancho_texto_visual(txt_num_pag, "helv", FONTSIZE)

                    if self.extra_page_num_pos == 'bottom_right':
                        x_pos = ancho_pagina - MARGEN_LATERAL - ancho_num_txt
                    else:  # bottom_center
                        x_pos = (ancho_pagina - ancho_num_txt) / 2.0

                    punto_num = fitz.Point(x_pos, y_posicion_bottom)
                    insertar_texto_orientado(pagina, punto_num, txt_num_pag, FONTSIZE, COLOR_ROJO)

                paginas_procesadas += 1

            # Guardar resultado respetando la jerarquía de carpetas
            rel_dir = os.path.dirname(rel_path)
            dir_destino = os.path.join(self.output_dir, rel_dir) if rel_dir else self.output_dir
            os.makedirs(dir_destino, exist_ok=True)

            ruta_salida = os.path.join(dir_destino, f"{nombre_archivo}")
            pdf_document.save(ruta_salida)
            pdf_document.close()

            return True, f"  ✅ Guardado: {ruta_salida}", paginas_procesadas

        except Exception as e:
            # 'paginas_procesadas' puede no existir si el error ocurrió
            # antes de abrir el documento (p. ej. archivo corrupto).
            paginas_hasta_el_error = locals().get("paginas_procesadas", 0)
            return False, f"  ❌ Error en {rel_path}: {str(e)}", paginas_hasta_el_error

    def run(self):
        total_archivos = len(self.pdf_items)
        tiempo_inicio = time.time()

        # Cuello de botella 1/5: abrir y guardar cada PDF es E/S de disco,
        # no cómputo puro. Mientras un hilo espera al disco, otro puede
        # seguir trabajando. Por eso se procesan varios archivos a la vez
        # en un pool de hilos, en lugar de uno por uno en secuencia.
        max_hilos = min(MAX_HILOS_SIMULTANEOS, total_archivos) or 1

        # Contador y candado propios del total de páginas: cada hilo del
        # pool termina un archivo en un momento distinto, así que hay que
        # protegerlo igual que el contador de progreso.
        total_paginas_procesadas = 0
        lock_paginas = threading.Lock()

        with ThreadPoolExecutor(max_workers=max_hilos) as executor:
            futuros = {
                executor.submit(self._procesar_un_archivo, item): item[1]
                for item in self.pdf_items
            }

            for futuro in as_completed(futuros):
                rel_path = futuros[futuro]
                try:
                    ok, mensaje, paginas = futuro.result()
                except Exception as e:
                    ok, mensaje, paginas = False, f"  ❌ Error inesperado en {rel_path}: {str(e)}", 0

                self.log.emit(f"\n📄 {rel_path}\n{mensaje}")

                with lock_paginas:
                    total_paginas_procesadas += paginas

                # El progreso se calcula de forma segura entre hilos: como
                # varios hilos terminan casi al mismo tiempo, se protege el
                # contador compartido con un candado para que no se pisen
                # los incrementos entre sí.
                with self._lock:
                    self._completados += 1
                    porcentaje = int((self._completados / total_archivos) * 100)
                self.progress.emit(porcentaje)

        tiempo_total = time.time() - tiempo_inicio
        self.log.emit(
            f"\n⏱ Tiempo total: {formatear_duracion(tiempo_total)}"
            f" — 📃 Páginas procesadas: {total_paginas_procesadas}"
        )
        self.proceso_terminado.emit(tiempo_total, total_paginas_procesadas)

# --- INTERFAZ GRÁFICA PRINCIPAL ---

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Indexador de PDFs - Leyendas y Encabezados")

        # El alto fijo de 820 px se salía de pantallas más bajas (portátiles,
        # resoluciones pequeñas) y, como no había forma de desplazarse, el
        # registro de texto y parte del botón quedaban fuera de la vista sin
        # ningún aviso. Ahora la ventana se ajusta al espacio disponible de
        # la pantalla en vez de a un tamaño fijo.
        pantalla = QApplication.primaryScreen().availableGeometry()
        ancho_inicial = min(750, pantalla.width() - 40)
        alto_inicial = min(820, pantalla.height() - 80)
        self.resize(ancho_inicial, alto_inicial)

        # Lista de tuplas: (ruta_absoluta_origen, ruta_relativa_para_la_salida)
        self.pdf_items = []
        self.init_menu()
        self.init_ui()

    def init_menu(self):
        menubar = self.menuBar()
        themes_menu = menubar.addMenu("🎨 Temas")

        action_default = QAction("Por Defecto (Sistema)", self)
        action_default.triggered.connect(lambda: self.apply_theme("default"))

        action_dark = QAction("Oscuro (Dark Mode)", self)
        action_dark.triggered.connect(lambda: self.apply_theme("dark"))

        action_minimal = QAction("Minimalista (Clean Light)", self)
        action_minimal.triggered.connect(lambda: self.apply_theme("minimal"))

        themes_menu.addAction(action_default)
        themes_menu.addAction(action_dark)
        themes_menu.addAction(action_minimal)

    def init_ui(self):
        # Todo el contenido va dentro de un área con scroll: si la pantalla
        # es más baja que lo que la interfaz necesita, aparece una barra de
        # desplazamiento en vez de que el registro de texto y el botón de
        # procesar queden fuera de la vista sin ninguna forma de llegar a
        # ellos.
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.NoFrame)
        self.setCentralWidget(scroll_area)

        central_widget = QWidget()
        scroll_area.setWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- SECCIÓN 1: SELECCIÓN DE ARCHIVOS Y DESTINO ---
        group_files = QGroupBox("1. Archivos PDF y Carpeta de Destino")
        layout_files = QVBoxLayout()

        layout_botones_sel = QHBoxLayout()
        btn_select_files = QPushButton("📄 Seleccionar Archivos PDF")
        btn_select_files.clicked.connect(self.select_files)
        btn_select_folder_in = QPushButton("📁 Seleccionar Carpeta (con subcarpetas)")
        btn_select_folder_in.clicked.connect(self.select_input_folder)
        btn_clear = QPushButton("🗑 Limpiar")
        btn_clear.clicked.connect(self.clear_selection)
        layout_botones_sel.addWidget(btn_select_files)
        layout_botones_sel.addWidget(btn_select_folder_in)
        layout_botones_sel.addWidget(btn_clear)

        self.list_files = QListWidget()
        self.list_files.setMaximumHeight(110)

        layout_folder = QHBoxLayout()
        self.input_output_dir = QLineEdit()
        self.input_output_dir.setPlaceholderText("Carpeta de salida para los archivos procesados...")
        btn_select_folder = QPushButton("Examinar...")
        btn_select_folder.clicked.connect(self.select_output_folder)
        layout_folder.addWidget(self.input_output_dir)
        layout_folder.addWidget(btn_select_folder)

        layout_files.addLayout(layout_botones_sel)
        layout_files.addWidget(self.list_files)
        layout_files.addLayout(layout_folder)
        group_files.setLayout(layout_files)
        main_layout.addWidget(group_files)

        # --- SECCIÓN 2: LEYENDAS IZQUIERDAS (SUPERIOR E INFERIOR) ---
        group_left = QGroupBox("2. Leyendas Izquierdas (Superior / Inferior)")
        layout_left = QVBoxLayout()

        # Leyenda Superior Izquierda
        self.chk_enable_left = QCheckBox("Poner Leyenda Superior Izquierda (Encabezado)")
        self.chk_enable_left.setChecked(True)
        self.chk_enable_left.toggled.connect(self.toggle_left_top_options)

        self.layout_left_input = QHBoxLayout()
        self.layout_left_input.setContentsMargins(20, 0, 0, 0)
        self.lbl_left = QLabel("Texto de la leyenda:")
        self.input_left_text = QLineEdit("DPOT")
        self.layout_left_input.addWidget(self.lbl_left)
        self.layout_left_input.addWidget(self.input_left_text)

        # Leyenda Inferior Izquierda
        self.chk_enable_bottom_left = QCheckBox("Poner Leyenda Inferior Izquierda (Pie de página)")
        self.chk_enable_bottom_left.setChecked(False)
        self.chk_enable_bottom_left.toggled.connect(self.toggle_left_bottom_options)

        self.layout_bottom_left_input = QHBoxLayout()
        self.layout_bottom_left_input.setContentsMargins(20, 0, 0, 0)
        self.lbl_bottom_left = QLabel("Texto de la leyenda:")
        self.lbl_bottom_left.setEnabled(False)
        self.input_bottom_left_text = QLineEdit()
        self.input_bottom_left_text.setPlaceholderText("Ejemplo: CONFIDENCIAL / PROYECTO 2026")
        self.input_bottom_left_text.setEnabled(False)
        self.layout_bottom_left_input.addWidget(self.lbl_bottom_left)
        self.layout_bottom_left_input.addWidget(self.input_bottom_left_text)

        layout_left.addWidget(self.chk_enable_left)
        layout_left.addLayout(self.layout_left_input)
        layout_left.addWidget(self.chk_enable_bottom_left)
        layout_left.addLayout(self.layout_bottom_left_input)

        group_left.setLayout(layout_left)
        main_layout.addWidget(group_left)

        # --- SECCIÓN 3: LEYENDA DERECHA Y NUMERACIÓN ---
        group_right = QGroupBox("3. Leyenda Derecha y Numeración de Páginas")
        layout_right = QVBoxLayout()

        self.chk_enable_right = QCheckBox("Poner Leyenda Derecha (Nombre / Texto)")
        self.chk_enable_right.setChecked(True)
        self.chk_enable_right.toggled.connect(self.toggle_right_options)

        self.widget_right_options = QWidget()
        layout_right_sub = QVBoxLayout(self.widget_right_options)
        layout_right_sub.setContentsMargins(20, 0, 0, 0)

        self.group_bg = QButtonGroup(self)

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

        # Aviso: la numeración es parte de la indexación (siempre va a la derecha)
        self.lbl_num_info = QLabel(
            "ℹ️ La numeración de páginas (ej. '1/10') forma parte de la indexación "
            "y se agrega siempre al final de esta leyenda superior derecha."
        )
        self.lbl_num_info.setWordWrap(True)
        self.lbl_num_info.setStyleSheet("color: #6c757d; font-style: italic;")
        layout_right_sub.addWidget(self.lbl_num_info)

        # Numeración ADICIONAL (opcional), solo en la parte inferior
        self.chk_extra_page_nums = QCheckBox("Repetir además la numeración en la parte inferior")
        self.chk_extra_page_nums.setChecked(False)
        self.chk_extra_page_nums.toggled.connect(self.toggle_page_num_options)

        self.layout_page_pos = QHBoxLayout()
        self.layout_page_pos.setContentsMargins(20, 0, 0, 0)
        self.lbl_page_pos = QLabel("Ubicación de la numeración adicional:")
        self.lbl_page_pos.setEnabled(False)
        self.combo_page_pos = QComboBox()
        self.combo_page_pos.addItem("Abajo a la Derecha", "bottom_right")
        self.combo_page_pos.addItem("Abajo al Centro", "bottom_center")
        self.combo_page_pos.setEnabled(False)

        self.layout_page_pos.addWidget(self.lbl_page_pos)
        self.layout_page_pos.addWidget(self.combo_page_pos)
        self.layout_page_pos.addStretch()

        # Formato del texto de la numeración adicional. Se ofrecen algunos
        # formatos ya armados y, además, la opción de escribir uno propio
        # usando los marcadores {pagina} y {total} (equivalente sencillo a
        # los comodines de Word/Excel, pero explícito en vez de símbolos
        # crípticos como # o &).
        self.layout_page_format = QHBoxLayout()
        self.layout_page_format.setContentsMargins(20, 0, 0, 0)
        self.lbl_page_format = QLabel("Formato del texto:")
        self.lbl_page_format.setEnabled(False)
        self.combo_page_format = QComboBox()
        self.combo_page_format.addItem("1/10", "{pagina}/{total}")
        self.combo_page_format.addItem("Página 1 de 10", "Página {pagina} de {total}")
        self.combo_page_format.addItem("PÁGINA 1 DE 10", "PÁGINA {pagina} DE {total}")
        self.combo_page_format.addItem("1 de 10", "{pagina} de {total}")
        self.combo_page_format.addItem("1 DE 10", "{pagina} DE {total}")
        self.combo_page_format.addItem("Personalizado...", "__custom__")
        self.combo_page_format.setEnabled(False)
        self.combo_page_format.currentIndexChanged.connect(self.toggle_custom_format_visibility)

        self.layout_page_format.addWidget(self.lbl_page_format)
        self.layout_page_format.addWidget(self.combo_page_format)
        self.layout_page_format.addStretch()

        self.layout_page_format_custom = QVBoxLayout()
        self.layout_page_format_custom.setContentsMargins(20, 0, 0, 0)
        self.input_page_format_custom = QLineEdit("{pagina}/{total}")
        self.input_page_format_custom.setPlaceholderText("Ej: Hoja {pagina} de {total}")
        self.input_page_format_custom.setVisible(False)
        self.input_page_format_custom.setEnabled(False)
        self.layout_page_format_custom.addWidget(self.input_page_format_custom)

        self.lbl_page_format_hint = QLabel(
            "Usa {pagina} y {total} donde quieras que aparezcan esos números."
        )
        self.lbl_page_format_hint.setWordWrap(True)
        self.lbl_page_format_hint.setStyleSheet("color: #6c757d; font-style: italic;")
        self.lbl_page_format_hint.setVisible(False)
        self.layout_page_format_custom.addWidget(self.lbl_page_format_hint)

        layout_right.addWidget(self.chk_enable_right)
        layout_right.addWidget(self.widget_right_options)
        layout_right.addWidget(self.chk_extra_page_nums)
        layout_right.addLayout(self.layout_page_pos)
        layout_right.addLayout(self.layout_page_format)
        layout_right.addLayout(self.layout_page_format_custom)

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
        self.log_output.setMinimumHeight(150)
        main_layout.addWidget(self.log_output)

    # --- ESTILOS / TEMAS ---

    def apply_theme(self, theme_name):
        if theme_name == "dark":
            dark_qss = """
                QMainWindow, QWidget { background-color: #1e1e2e; color: #cdd6f4; font-size: 13px; }
                QMenuBar { background-color: #181825; color: #cdd6f4; border-bottom: 1px solid #313244; }
                QMenuBar::item:selected { background-color: #313244; }
                QMenu { background-color: #181825; color: #cdd6f4; border: 1px solid #45475a; }
                QMenu::item:selected { background-color: #313244; }
                QGroupBox { font-weight: bold; border: 1px solid #45475a; border-radius: 6px; margin-top: 10px; color: #89b4fa; padding-top: 12px; }
                QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
                QLineEdit, QSpinBox, QTextEdit, QListWidget, QComboBox { background-color: #313244; color: #cdd6f4; border: 1px solid #45475a; border-radius: 4px; padding: 5px; }
                QPushButton { background-color: #89b4fa; color: #11111b; font-weight: bold; border-radius: 4px; padding: 6px 12px; }
                QPushButton:hover { background-color: #b4befe; }
                QPushButton:disabled { background-color: #45475a; color: #7f849c; }
                QProgressBar { border: 1px solid #45475a; border-radius: 4px; text-align: center; color: #cdd6f4; background-color: #313244; }
                QProgressBar::chunk { background-color: #a6e3a1; }
                QCheckBox, QRadioButton { color: #cdd6f4; }
            """
            self.setStyleSheet(dark_qss)
        elif theme_name == "minimal":
            minimal_qss = """
                QMainWindow, QWidget { background-color: #f8f9fa; color: #212529; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
                QMenuBar { background-color: #ffffff; color: #212529; border-bottom: 1px solid #dee2e6; }
                QMenuBar::item:selected { background-color: #e9ecef; }
                QMenu { background-color: #ffffff; color: #212529; border: 1px solid #ced4da; }
                QMenu::item:selected { background-color: #e9ecef; }
                QGroupBox { font-weight: 600; border: 1px solid #dee2e6; border-radius: 8px; margin-top: 12px; color: #0d6efd; background-color: #ffffff; padding-top: 12px; }
                QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
                QLineEdit, QSpinBox, QTextEdit, QListWidget, QComboBox { background-color: #ffffff; color: #212529; border: 1px solid #ced4da; border-radius: 6px; padding: 5px; }
                QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid #0d6efd; }
                QPushButton { background-color: #0d6efd; color: #ffffff; font-weight: 600; border-radius: 6px; padding: 8px 14px; border: none; }
                QPushButton:hover { background-color: #0b5ed7; }
                QPushButton:disabled { background-color: #ced4da; color: #6c757d; }
                QProgressBar { border: none; border-radius: 6px; text-align: center; color: #ffffff; background-color: #e9ecef; }
                QProgressBar::chunk { background-color: #198754; border-radius: 6px; }
                QCheckBox, QRadioButton { color: #212529; }
            """
            self.setStyleSheet(minimal_qss)
        else:
            self.setStyleSheet("")

    # --- CONTROL DE INTERFAZ ---

    def toggle_left_top_options(self, checked):
        self.lbl_left.setEnabled(checked)
        self.input_left_text.setEnabled(checked)

    def toggle_left_bottom_options(self, checked):
        self.lbl_bottom_left.setEnabled(checked)
        self.input_bottom_left_text.setEnabled(checked)

    def toggle_right_options(self, checked):
        self.widget_right_options.setEnabled(checked)

    def toggle_page_num_options(self, checked):
        self.lbl_page_pos.setEnabled(checked)
        self.combo_page_pos.setEnabled(checked)
        self.lbl_page_format.setEnabled(checked)
        self.combo_page_format.setEnabled(checked)
        # El campo de texto personalizado solo se habilita si además el
        # usuario eligió "Personalizado..." en el combo de formato.
        es_personalizado = checked and self.combo_page_format.currentData() == "__custom__"
        self.input_page_format_custom.setEnabled(es_personalizado)

    def toggle_custom_format_visibility(self):
        es_personalizado = self.combo_page_format.currentData() == "__custom__"
        self.input_page_format_custom.setVisible(es_personalizado)
        self.lbl_page_format_hint.setVisible(es_personalizado)
        self.input_page_format_custom.setEnabled(es_personalizado and self.chk_extra_page_nums.isChecked())

    def _agregar_items(self, nuevos):
        """Agrega tuplas (ruta_origen, ruta_relativa) evitando duplicados.

        Cuello de botella 3: con carpetas de miles de PDFs, llamar
        list_files.addItem() una vez por archivo obliga al widget a
        recalcular su layout y repintarse en cada llamada. En vez de eso,
        se arma primero la lista completa de textos en memoria (barato) y
        se entrega al widget de una sola vez con addItems().
        """
        existentes = {os.path.normcase(os.path.abspath(p)) for p, _ in self.pdf_items}
        nuevos_textos = []

        for origen, relativo in nuevos:
            clave = os.path.normcase(os.path.abspath(origen))
            if clave in existentes:
                continue
            existentes.add(clave)
            self.pdf_items.append((origen, relativo))
            nuevos_textos.append(relativo)

        if nuevos_textos:
            self.list_files.addItems(nuevos_textos)

        return len(nuevos_textos)

    def clear_selection(self):
        self.pdf_items = []
        self.list_files.clear()

    def select_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Seleccionar PDFs", "", "Archivos PDF (*.pdf)")
        if not files:
            return
        # Archivos sueltos: se generan directamente en la raíz de la carpeta de salida
        nuevos = [(f, os.path.basename(f)) for f in files]
        self._agregar_items(nuevos)

    def select_input_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar Carpeta con PDFs")
        if not folder:
            return

        folder = os.path.abspath(folder)
        # La carpeta padre seleccionada se recrea dentro de la carpeta de salida
        base = os.path.dirname(folder)

        nuevos = []
        for raiz, _dirs, archivos in os.walk(folder):
            for nombre in sorted(archivos):
                if nombre.lower().endswith(".pdf"):
                    origen = os.path.join(raiz, nombre)
                    relativo = os.path.relpath(origen, base)
                    nuevos.append((origen, relativo))

        if not nuevos:
            QMessageBox.information(self, "Sin resultados",
                                    "No se encontraron archivos PDF en esa carpeta ni en sus subcarpetas.")
            return

        agregados = self._agregar_items(nuevos)
        QMessageBox.information(self, "Carpeta agregada",
                                f"Se agregaron {agregados} archivo(s) PDF de:\n{folder}")

    def select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar Carpeta de Salida")
        if folder:
            self.input_output_dir.setText(folder)

    # --- PROCESAMIENTO ---

    def start_processing(self):
        if not self.pdf_items:
            QMessageBox.warning(self, "Atención", "Selecciona al menos un archivo PDF o una carpeta.")
            return

        output_dir = self.input_output_dir.text().strip()
        if not output_dir or not os.path.isdir(output_dir):
            QMessageBox.warning(self, "Atención", "Selecciona una carpeta de salida válida.")
            return

        output_dir = os.path.abspath(output_dir)

        # Evita procesar archivos que ya estén dentro de la carpeta de salida
        items = [(o, r) for o, r in self.pdf_items if not ruta_dentro_de(o, output_dir)]
        if not items:
            QMessageBox.warning(self, "Atención",
                                "Todos los archivos seleccionados están dentro de la carpeta de salida. "
                                "Elige una carpeta de salida distinta.")
            return

        right_mode = 'delimiter' if self.radio_delim.isChecked() else 'custom'
        page_num_pos = self.combo_page_pos.currentData()

        # Si eligió "Personalizado...", se usa lo que escribió en el campo
        # de texto; si lo dejó vacío, se cae al formato clásico "num/total"
        # en vez de estampar un texto vacío por accidente.
        if self.combo_page_format.currentData() == "__custom__":
            page_num_format = self.input_page_format_custom.text().strip() or "{pagina}/{total}"
        else:
            page_num_format = self.combo_page_format.currentData()

        self.btn_process.setEnabled(False)
        self.progress_bar.setValue(0)
        self.log_output.clear()

        self.worker = WorkerThread(
            pdf_items=items,
            output_dir=output_dir,
            enable_left=self.chk_enable_left.isChecked(),
            left_text=self.input_left_text.text(),
            enable_bottom_left=self.chk_enable_bottom_left.isChecked(),
            bottom_left_text=self.input_bottom_left_text.text(),
            enable_right=self.chk_enable_right.isChecked(),
            right_mode=right_mode,
            delimiter=self.input_delimiter.text(),
            block_idx=self.spin_block.value(),
            custom_text=self.input_custom_text.text(),
            enable_extra_page_nums=self.chk_extra_page_nums.isChecked(),
            extra_page_num_pos=page_num_pos,
            extra_page_num_format=page_num_format
        )

        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.log.connect(self.log_output.append)
        self.worker.proceso_terminado.connect(self.on_processing_finished)
        self.worker.start()

    def on_processing_finished(self, tiempo_total, total_paginas):
        self.btn_process.setEnabled(True)
        QMessageBox.information(
            self, "Éxito",
            f"¡Procesamiento completado correctamente!\n\n"
            f"Tiempo total: {formatear_duracion(tiempo_total)}\n"
            f"Páginas procesadas: {total_paginas}"
        )

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
