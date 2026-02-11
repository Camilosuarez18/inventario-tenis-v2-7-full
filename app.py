import os
import io
import zipfile
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, CheckConstraint, func, or_
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import pandas as pd
import logging
from PIL import Image
from markupsafe import Markup

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join('static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'cambia-esta-clave')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'inventario_tenis.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024

logging.getLogger('werkzeug').setLevel(logging.WARNING)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

db = SQLAlchemy(app)

# ---------- Utils ----------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def norm_text(s: str) -> str:
    s = (s or '').strip().lower()
    parts = s.split()
    return ' '.join(parts)

def process_image(path):
    try:
        with Image.open(path) as im:
            if im.mode in ("RGBA","P"): im = im.convert("RGB")
            im.thumbnail((1200,1200))
            ext = os.path.splitext(path)[1].lower()
            if ext in ['.jpg','.jpeg']:
                im.save(path, format='JPEG', quality=85, optimize=True)
            elif ext == '.webp':
                im.save(path, format='WEBP', quality=85, method=6)
            elif ext == '.png':
                im.save(path, format='PNG', optimize=True)
            else:
                new_path = os.path.splitext(path)[0] + '.jpg'
                im.save(new_path, format='JPEG', quality=85, optimize=True)
                try: os.remove(path)
                except Exception: pass
                return os.path.basename(new_path)
    except Exception as e:
        app.logger.warning(f"No se pudo procesar la imagen {path}: {e}")
    return os.path.basename(path)

def save_image(file):
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        ts = datetime.now().strftime('%Y%m%d%H%M%S%f')
        name, ext = os.path.splitext(filename)
        new_name = f"{ts}_{name}{ext.lower()}"
        path = os.path.join(app.config['UPLOAD_FOLDER'], new_name)
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        file.save(path)
        return process_image(path)
    return None

def save_image_from_bytes(filename, data: bytes):
    if not allowed_file(filename):
        return None
    filename = secure_filename(os.path.basename(filename))
    ts = datetime.now().strftime('%Y%m%d%H%M%S%f')
    name, ext = os.path.splitext(filename)
    new_name = f"{ts}_{name}{ext.lower()}"
    path = os.path.join(app.config['UPLOAD_FOLDER'], new_name)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)
    final = process_image(path)
    return final

# ---------- Models ----------
class Usuario(UserMixin, db.Model):
    __tablename__ = 'usuarios'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    rol = db.Column(db.String(50), nullable=False, default='vendedor')

class Tienda(db.Model):
    __tablename__ = 'tiendas'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False, unique=True)
    direccion = db.Column(db.String(255))
    inventarios = db.relationship('Inventario', back_populates='tienda', cascade="all, delete-orphan")

class ProductoBase(db.Model):
    __tablename__ = 'productos_base'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(200), nullable=False)
    marca = db.Column(db.String(120))
    descripcion = db.Column(db.Text)
    variantes = db.relationship('ProductoVariante', back_populates='base', cascade="all, delete-orphan")

class ProductoVariante(db.Model):
    __tablename__ = 'productos_variantes'
    id = db.Column(db.Integer, primary_key=True)
    base_id = db.Column(db.Integer, db.ForeignKey('productos_base.id'), nullable=False)
    talla = db.Column(db.String(20), nullable=False)
    color = db.Column(db.String(50))
    sku = db.Column(db.String(80), unique=True)
    precio = db.Column(db.Float, default=0)
    imagen_filename = db.Column(db.String(255))
    base = db.relationship('ProductoBase', back_populates='variantes')
    inventarios = db.relationship('Inventario', back_populates='variante', cascade="all, delete-orphan")
    movimientos = db.relationship('Movimiento', back_populates='variante')

class Inventario(db.Model):
    __tablename__ = 'inventarios'
    id = db.Column(db.Integer, primary_key=True)
    tienda_id = db.Column(db.Integer, db.ForeignKey('tiendas.id'), nullable=False)
    variante_id = db.Column(db.Integer, db.ForeignKey('productos_variantes.id'), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False, default=0)
    tienda = db.relationship('Tienda', back_populates='inventarios')
    variante = db.relationship('ProductoVariante', back_populates='inventarios')
    __table_args__ = (
        UniqueConstraint('tienda_id','variante_id', name='uq_tienda_variante'),
        CheckConstraint('cantidad >= 0', name='ck_cantidad_no_negativa'),
    )

class Movimiento(db.Model):
    __tablename__ = 'movimientos'
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    tipo = db.Column(db.String(20), nullable=False)  # ENTRADA/SALIDA/TRASLADO
    cantidad = db.Column(db.Integer, nullable=False)
    nota = db.Column(db.String(255))
    usuario_nombre = db.Column(db.String(120))
    variante_id = db.Column(db.Integer, db.ForeignKey('productos_variantes.id'), nullable=False)
    origen_tienda_id = db.Column(db.Integer, db.ForeignKey('tiendas.id'))
    destino_tienda_id = db.Column(db.Integer, db.ForeignKey('tiendas.id'))
    variante = db.relationship('ProductoVariante', back_populates='movimientos')
    origen_tienda = db.relationship('Tienda', foreign_keys=[origen_tienda_id])
    destino_tienda = db.relationship('Tienda', foreign_keys=[destino_tienda_id])
    __table_args__ = (
        CheckConstraint("tipo in ('ENTRADA','SALIDA','TRASLADO')", name='ck_tipo_valido'),
        CheckConstraint('cantidad > 0', name='ck_cantidad_positiva'),
    )

@login_manager.user_loader
def load_user(user_id):
    return Usuario.query.get(int(user_id))

# ---------- Auth helper ----------
def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if current_user.rol != 'admin':
            flash('No tienes permisos de administrador.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return wrapper

# ---------- Auth ----------
@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        nombre = request.form.get('nombre','').strip()
        password = request.form.get('password','')
        user = Usuario.query.filter_by(nombre=nombre).first()
        if not user or not check_password_hash(user.password, password):
            flash('Usuario o contraseña incorrectos', 'danger')
            return redirect(url_for('login'))
        login_user(user)
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# ---------- Dashboard ----------
@app.route('/')
@login_required
def index():
    total_bases = db.session.query(func.count(ProductoBase.id)).scalar()
    total_variantes = db.session.query(func.count(ProductoVariante.id)).scalar()
    total_tiendas = db.session.query(func.count(Tienda.id)).scalar()
    total_movs = db.session.query(func.count(Movimiento.id)).scalar()

    hoy = datetime.utcnow().date()
    inicio = hoy - timedelta(days=29)
    fechas = [inicio + timedelta(days=i) for i in range(30)]
    entradas = {d:0 for d in fechas}
    salidas = {d:0 for d in fechas}

    movs_30 = Movimiento.query.filter(Movimiento.fecha >= datetime.combine(inicio, datetime.min.time())).all()
    for m in movs_30:
        d = m.fecha.date()
        if d in entradas:
            if m.tipo=='ENTRADA' or (m.tipo=='TRASLADO' and m.destino_tienda_id): entradas[d]+=m.cantidad
            if m.tipo=='SALIDA' or (m.tipo=='TRASLADO' and m.origen_tienda_id): salidas[d]+=m.cantidad

    labels = [d.strftime('%Y-%m-%d') for d in fechas]
    data_entradas = [entradas[d] for d in fechas]
    data_salidas = [salidas[d] for d in fechas]

    top_salidas = (
        db.session.query(ProductoVariante, ProductoBase, func.sum(Movimiento.cantidad).label('total'))
        .join(ProductoVariante, Movimiento.variante_id==ProductoVariante.id)
        .join(ProductoBase, ProductoVariante.base_id==ProductoBase.id)
        .filter((Movimiento.tipo=='SALIDA') | (Movimiento.tipo=='TRASLADO'))
        .filter(Movimiento.fecha >= datetime.combine(inicio, datetime.min.time()))
        .group_by(ProductoVariante.id)
        .order_by(func.sum(Movimiento.cantidad).desc())
        .limit(5).all()
    )
    top_labels = [f"{pb.nombre} - {v.talla} - {v.color or ''}" for v,pb,_ in top_salidas]
    top_values = [int(t) for *_,t in top_salidas]

    return render_template('index.html', total_bases=total_bases, total_variantes=total_variantes,
                           total_tiendas=total_tiendas, total_movs=total_movs,
                           chart_labels=labels, chart_entradas=data_entradas, chart_salidas=data_salidas,
                           top_labels=top_labels, top_values=top_values)

# ---------- Usuarios (ADMIN) ----------
@app.route('/usuarios')
@login_required
@admin_required
def usuarios_list():
    usuarios = Usuario.query.order_by(Usuario.id.asc()).all()
    return render_template('usuarios_list.html', usuarios=usuarios)

@app.route('/usuarios/nuevo', methods=['GET','POST'])
@login_required
@admin_required
def usuarios_nuevo():
    if request.method=='POST':
        nombre = request.form.get('nombre','').strip()
        rol = request.form.get('rol','vendedor')
        pwd = request.form.get('password','')
        pwd2 = request.form.get('password2','')
        if not nombre or len(nombre)<4:
            flash('El nombre de usuario debe tener al menos 4 caracteres.', 'danger'); return redirect(url_for('usuarios_nuevo'))
        if Usuario.query.filter(func.lower(Usuario.nombre)==nombre.lower()).first():
            flash('Ese usuario ya existe.', 'danger'); return redirect(url_for('usuarios_nuevo'))
        if pwd!=pwd2 or len(pwd)<6:
            flash('Las contraseñas no coinciden o son muy cortas (>=6).', 'danger'); return redirect(url_for('usuarios_nuevo'))
        u = Usuario(nombre=nombre, password=generate_password_hash(pwd), rol=rol)
        db.session.add(u); db.session.commit(); flash('Usuario creado.', 'success')
        return redirect(url_for('usuarios_list'))
    return render_template('usuarios_form.html')

@app.route('/usuarios/<int:user_id>/rol', methods=['GET','POST'])
@login_required
@admin_required
def usuarios_cambiar_rol(user_id):
    u = Usuario.query.get_or_404(user_id)
    if request.method=='POST':
        rol = request.form.get('rol','vendedor')
        if u.id==1 and rol!='admin':
            flash('No puedes quitar el rol admin del administrador principal.', 'warning'); return redirect(url_for('usuarios_cambiar_rol', user_id=user_id))
        u.rol=rol; db.session.commit(); flash('Rol actualizado.', 'success')
        return redirect(url_for('usuarios_list'))
    return render_template('usuarios_rol.html', user=u)

@app.route('/usuarios/<int:user_id>/password', methods=['GET','POST'])
@login_required
@admin_required
def usuarios_password(user_id):
    u = Usuario.query.get_or_404(user_id)
    if request.method=='POST':
        pwd = request.form.get('password',''); pwd2 = request.form.get('password2','')
        if pwd!=pwd2 or len(pwd)<6:
            flash('Las contraseñas no coinciden o son muy cortas (>=6).', 'danger'); return redirect(url_for('usuarios_password', user_id=user_id))
        u.password = generate_password_hash(pwd); db.session.commit(); flash('Contraseña actualizada.', 'success')
        return redirect(url_for('usuarios_list'))
    return render_template('usuarios_password.html', user=u)

@app.route('/usuarios/<int:user_id>/eliminar', methods=['GET','POST'])
@login_required
@admin_required
def usuarios_eliminar(user_id):
    u = Usuario.query.get_or_404(user_id)
    if request.method=='POST':
        if u.id==1:
            flash('No puedes eliminar el administrador principal.', 'warning'); return redirect(url_for('usuarios_list'))
        db.session.delete(u); db.session.commit(); flash('Usuario eliminado.', 'success')
        return redirect(url_for('usuarios_list'))
    return render_template('usuarios_delete.html', user=u)

# ---------- Productos & Variantes ----------
@app.route('/productos')
@login_required
def productos_list():
    bases = ProductoBase.query.order_by(ProductoBase.marca, ProductoBase.nombre).all()
    return render_template('productos.html', bases=bases)

# En v2.7 dejamos la pantalla de Variantes (buscar) para soporte
@app.route('/variantes')
@login_required
def variantes_list():
    q = request.args.get('q','').strip()
    marca = request.args.get('marca','').strip()
    talla = request.args.get('talla','').strip()
    color = request.args.get('color','').strip()
    sku = request.args.get('sku','').strip()
    page = max(1, request.args.get('page', default=1, type=int))
    per_page = min(200, max(5, request.args.get('per_page', default=25, type=int)))

    qry = db.session.query(ProductoVariante, ProductoBase).join(ProductoBase, ProductoVariante.base_id==ProductoBase.id)

    if q:
        ql = norm_text(q)
        qry = qry.filter(or_(
            func.lower(func.trim(ProductoBase.nombre)).like(f"%{ql}%"),
            func.lower(func.trim(func.coalesce(ProductoBase.marca, ''))).like(f"%{ql}%"),
            func.lower(func.trim(func.coalesce(ProductoVariante.sku, ''))).like(f"%{ql}%"),
            func.lower(func.trim(func.coalesce(ProductoVariante.color, ''))).like(f"%{ql}%"),
            func.lower(func.trim(func.coalesce(ProductoVariante.talla, ''))).like(f"%{ql}%")
        ))
    if marca:
        qry = qry.filter(func.lower(func.trim(func.coalesce(ProductoBase.marca, ''))) == norm_text(marca))
    if talla:
        qry = qry.filter(func.lower(func.trim(func.coalesce(ProductoVariante.talla, ''))) == norm_text(talla))
    if color:
        qry = qry.filter(func.lower(func.trim(func.coalesce(ProductoVariante.color, ''))) == norm_text(color))
    if sku:
        qry = qry.filter(func.lower(func.trim(func.coalesce(ProductoVariante.sku, ''))) == norm_text(sku))

    total = qry.count()
    items = (qry.order_by(ProductoBase.marca.nullsfirst(), ProductoBase.nombre, ProductoVariante.talla, ProductoVariante.color)
                .offset((page-1)*per_page).limit(per_page).all())
    total_pages = (total + per_page - 1)//per_page

    marcas = [m[0] for m in db.session.query(ProductoBase.marca).distinct().order_by(ProductoBase.marca).all() if m[0]]
    tallas = [t[0] for t in db.session.query(ProductoVariante.talla).distinct().order_by(ProductoVariante.talla).all() if t[0]]
    colores = [c[0] for c in db.session.query(ProductoVariante.color).distinct().order_by(ProductoVariante.color).all() if c[0]]

    return render_template('variantes_list.html', items=items, total=total, page=page, per_page=per_page,
                           total_pages=total_pages, q=q, marca=marca, talla=talla, color=color, sku=sku,
                           marcas=marcas, tallas=tallas, colores=colores)

@app.route('/productos/base/nuevo', methods=['POST'])
@login_required
@admin_required
def producto_base_nuevo():
    nombre = request.form.get('nombre','')
    marca = request.form.get('marca','')
    descripcion = request.form.get('descripcion','')

    # v2.7: validación básica (no robusta) — posible duplicado si marca vacía vs NULL
    if not nombre.strip():
        flash('El nombre del producto es obligatorio.', 'danger')
        return redirect(url_for('productos_list'))

    existente = (ProductoBase.query.filter(
        func.lower(func.trim(ProductoBase.nombre)) == nombre.strip().lower(),
        func.lower(func.trim(ProductoBase.marca)) == (marca.strip().lower() or None)
    ).first())

    if existente:
        flash('⚠️ Ya existe un producto con ese nombre y marca.', 'warning')
        return redirect(url_for('productos_list'))

    b = ProductoBase(nombre=nombre.strip(), marca=(marca.strip() or None), descripcion=descripcion.strip() or None)
    db.session.add(b); db.session.commit(); flash('Producto base creado.', 'success')
    return redirect(url_for('productos_list'))

@app.route('/productos/base/<int:base_id>/eliminar', methods=['POST'])
@login_required
@admin_required
def producto_base_eliminar(base_id):
    b = ProductoBase.query.get_or_404(base_id)
    db.session.delete(b); db.session.commit()
    flash('Producto base eliminado.', 'success')
    return redirect(url_for('productos_list'))

@app.route('/productos/base/<int:base_id>/editar', methods=['GET','POST'])
@login_required
@admin_required
def producto_base_editar(base_id):
    b = ProductoBase.query.get_or_404(base_id)
    if request.method=='POST':
        nuevo_nombre = request.form.get('nombre','')
        nueva_marca = request.form.get('marca','')
        b.descripcion = (request.form.get('descripcion','') or '').strip() or None

        if not nuevo_nombre.strip():
            flash('El nombre del producto es obligatorio.', 'danger')
            return redirect(url_for('producto_base_editar', base_id=base_id))

        dup = (ProductoBase.query.filter(
            func.lower(func.trim(ProductoBase.nombre)) == nuevo_nombre.strip().lower(),
            func.lower(func.trim(ProductoBase.marca)) == (nueva_marca.strip().lower() or None),
            ProductoBase.id != base_id
        ).first())
        if dup:
            flash('⚠️ Ya existe otro producto con ese nombre y marca.', 'warning')
            return redirect(url_for('producto_base_editar', base_id=base_id))

        b.nombre = nuevo_nombre.strip(); b.marca = (nueva_marca.strip() or None)
        db.session.commit(); flash('Producto base actualizado.', 'success')
        return redirect(url_for('productos_list'))
    return render_template('producto_base_edit.html', base=b)

@app.route('/productos/variante/nueva', methods=['POST'])
@login_required
@admin_required
def producto_variante_nueva():
    base_id = request.form.get('base_id', type=int)
    talla = request.form.get('talla','').strip()
    color = request.form.get('color','').strip() or None
    sku = request.form.get('sku','').strip() or None
    precio = float(request.form.get('precio',0) or 0)
    img = request.files.get('imagen')
    imagen_filename = save_image(img) if img and img.filename else None
    if not base_id or not talla:
        flash('Base y talla son obligatorios.', 'danger')
        return redirect(url_for('productos_list'))
    v = ProductoVariante(base_id=base_id, talla=talla, color=color, sku=sku, precio=precio, imagen_filename=imagen_filename)
    db.session.add(v)
    try:
        db.session.commit(); flash('Variante creada.', 'success')
    except Exception as e:
        db.session.rollback(); flash(f'Error al crear variante: {e}', 'danger')
    return redirect(url_for('productos_list'))

@app.route('/productos/variante/<int:variante_id>/eliminar', methods=['POST'])
@login_required
@admin_required
def producto_variante_eliminar(variante_id):
    v = ProductoVariante.query.get_or_404(variante_id)
    db.session.delete(v); db.session.commit()
    flash('Variante eliminada.', 'success')
    return redirect(url_for('productos_list'))

@app.route('/productos/variante/<int:variante_id>/editar', methods=['GET','POST'])
@login_required
@admin_required
def producto_variante_editar(variante_id):
    v = ProductoVariante.query.get_or_404(variante_id)
    b = v.base
    if request.method=='POST':
        v.talla = request.form.get('talla','').strip()
        v.color = request.form.get('color','').strip() or None
        v.sku = request.form.get('sku','').strip() or None
        v.precio = float(request.form.get('precio',0) or 0)
        img = request.files.get('imagen')
        if img and img.filename:
            new_name = save_image(img)
            if new_name: v.imagen_filename = new_name
            else:
                flash('El archivo no es una imagen válida.', 'danger')
                return redirect(url_for('producto_variante_editar', variante_id=variante_id))
        if not v.talla:
            flash('La talla es obligatoria.', 'danger')
            return redirect(url_for('producto_variante_editar', variante_id=variante_id))
        try:
            db.session.commit(); flash('Variante actualizada.', 'success')
        except Exception as e:
            db.session.rollback(); flash(f'No se pudo actualizar: {e}', 'danger')
        return redirect(url_for('productos_list'))
    return render_template('producto_variante_edit.html', var=v, base=b)

@app.route('/productos/variante/<int:variante_id>/quitar_imagen', methods=['POST'])
@login_required
@admin_required
def producto_variante_quitar_imagen(variante_id):
    v = ProductoVariante.query.get_or_404(variante_id)
    if v.imagen_filename:
        try:
            path = os.path.join(app.config['UPLOAD_FOLDER'], v.imagen_filename)
            if os.path.exists(path): os.remove(path)
        except Exception as e:
            app.logger.warning(f'No se pudo eliminar imagen física: {e}')
        v.imagen_filename = None; db.session.commit(); flash('Imagen eliminada de la variante.', 'success')
    else:
        flash('La variante no tiene imagen asignada.', 'info')
    return redirect(url_for('producto_variante_editar', variante_id=variante_id))

# ---------- Importación ----------
@app.route('/importar', methods=['GET','POST'])
@login_required
@admin_required
def importar():
    if request.method=='POST':
        f = request.files.get('archivo')
        if not f or not f.filename:
            flash('Selecciona un archivo .xlsx o .csv', 'danger'); return redirect(url_for('importar'))
        ext = os.path.splitext(f.filename)[1].lower()
        try:
            if ext=='.xlsx': df = pd.read_excel(f, engine='openpyxl')
            elif ext=='.csv': df = pd.read_csv(f)
            else:
                flash('Formato no soportado. Usa .xlsx o .csv', 'danger'); return redirect(url_for('importar'))
        except Exception as e:
            flash(f'No se pudo leer el archivo: {e}', 'danger'); return redirect(url_for('importar'))
        df.columns = [c.lower() for c in df.columns]
        cols = {'base_nombre','marca','descripcion','talla','color','sku','precio'}
        if not cols.issubset(set(df.columns)):
            flash('Columnas requeridas: base_nombre, marca, descripcion, talla, color, sku, precio', 'danger'); return redirect(url_for('importar'))
        creados_b=creados_v=actualizados_v=errores=0
        for idx,row in df.iterrows():
            try:
                base_nombre = str(row.get('base_nombre') or '')
                marca = str(row.get('marca') or '')
                descripcion = str(row.get('descripcion') or '')
                talla = str(row.get('talla') or '').strip()
                color = str(row.get('color') or '').strip() or None
                sku = str(row.get('sku') or '').strip() or None
                precio = float(row.get('precio') or 0)
                if not base_nombre.strip():
                    raise ValueError('base_nombre requerido')
                base = ProductoBase.query.filter(
                    func.lower(func.trim(ProductoBase.nombre)) == base_nombre.strip().lower(),
                    func.lower(func.trim(ProductoBase.marca)) == (marca.strip().lower() or None)
                ).first()
                if not base:
                    base = ProductoBase(nombre=base_nombre.strip(), marca=(marca.strip() or None), descripcion=(descripcion.strip() or None))
                    db.session.add(base); db.session.flush(); creados_b+=1
                v = None
                if sku:
                    v = ProductoVariante.query.filter_by(sku=sku).first()
                if v:
                    v.talla = talla or v.talla; v.color = color; v.precio = precio
                    if v.base_id!=base.id: v.base_id=base.id
                    actualizados_v+=1
                else:
                    if not talla: raise ValueError('talla requerida')
                    v = ProductoVariante(base_id=base.id, talla=talla, color=color, sku=sku, precio=precio)
                    db.session.add(v); creados_v+=1
            except Exception as e:
                errores+=1; app.logger.warning(f"Fila {idx+1} error: {e}")
        try:
            db.session.commit(); flash(f'Importación: Bases creadas {creados_b}, Variantes creadas {creados_v}, Variantes actualizadas {actualizados_v}, Errores {errores}', 'success')
        except Exception as e:
            db.session.rollback(); flash(f'Error al guardar importación: {e}', 'danger')
        return redirect(url_for('productos_list'))
    return render_template('importar.html')

@app.route('/plantilla_importacion')
@login_required
@admin_required
def plantilla_importacion():
    path = os.path.join(BASE_DIR,'plantilla_importacion.xlsx')
    if not os.path.isfile(path):
        df = pd.DataFrame([{'base_nombre':'Tenis Runner X','marca':'Speedy','descripcion':'Tenis para correr','talla':'40','color':'Negro','sku':'RUNX-40-NG','precio':299900}])
        df.to_excel(path, index=False, engine='openpyxl')
    return send_file(path, as_attachment=True)

# ---------- Imágenes ZIP + Reporte CSV ----------
@app.route('/imagenes/cargar_zip', methods=['GET','POST'])
@login_required
@admin_required
def imagenes_cargar_zip():
    if request.method == 'POST':
        f = request.files.get('archivo')
        if not f or not f.filename:
            flash('Selecciona un archivo .zip', 'danger'); return redirect(url_for('imagenes_cargar_zip'))
        if not f.filename.lower().endswith('.zip'):
            flash('Formato no soportado. Sube un archivo .zip', 'danger'); return redirect(url_for('imagenes_cargar_zip'))
        try:
            data = f.read(); zf = zipfile.ZipFile(io.BytesIO(data))
        except Exception as e:
            flash(f'No se pudo abrir el ZIP: {e}', 'danger'); return redirect(url_for('imagenes_cargar_zip'))
        updated = not_found = invalid = errors = 0
        rows = []
        for name in zf.namelist():
            if name.endswith('/'):
                continue
            base = os.path.basename(name)
            ext = os.path.splitext(base)[1].lower()
            sku_name = os.path.splitext(base)[0].strip()
            estado = ''
            img_final = ''
            err_txt = ''
            if not sku_name:
                continue
            if ext.replace('.', '') not in ALLOWED_EXTENSIONS:
                invalid += 1; estado = 'Extensión inválida'
                rows.append({'SKU': sku_name, 'Archivo ZIP': base, 'Estado': estado, 'Imagen guardada': img_final, 'Error': err_txt}); continue
            try:
                with zf.open(name) as img_file: content = img_file.read()
                v = ProductoVariante.query.filter_by(sku=sku_name).first()
                if not v:
                    not_found += 1; estado = 'SKU no encontrado'
                    rows.append({'SKU': sku_name, 'Archivo ZIP': base, 'Estado': estado, 'Imagen guardada': img_final, 'Error': err_txt}); continue
                saved = save_image_from_bytes(base, content)
                if saved:
                    v.imagen_filename = saved; updated += 1; estado = 'Actualizado'; img_final = saved
                else:
                    estado = 'Error'; errors += 1
                rows.append({'SKU': sku_name, 'Archivo ZIP': base, 'Estado': estado, 'Imagen guardada': img_final, 'Error': err_txt})
            except Exception as e:
                errors += 1; estado = 'Error'; err_txt = str(e)
                rows.append({'SKU': sku_name, 'Archivo ZIP': base, 'Estado': estado, 'Imagen guardada': img_final, 'Error': err_txt}); app.logger.warning(f'Error con {name}: {e}')
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback(); flash(f'Error al guardar cambios: {e}', 'danger'); return redirect(url_for('imagenes_cargar_zip'))
        ts = datetime.now().strftime('%Y%m%d%H%M%S')
        report_name = f'reporte_carga_imagenes_{ts}.csv'
        report_path = os.path.join(BASE_DIR, report_name)
        try:
            df = pd.DataFrame(rows); df.to_csv(report_path, index=False, encoding='utf-8-sig')
            link = url_for('imagenes_reporte_zip', file=report_name)
            flash(Markup(f"ZIP procesado. Imágenes actualizadas: {updated} | SKU no encontrados: {not_found} | Extensiones inválidas: {invalid} | Errores: {errors}. "
                         f"<a href='{link}' class='alert-link'>Descargar reporte CSV</a>"), 'success')
        except Exception as e:
            flash(f'Se procesó el ZIP pero no se pudo generar el reporte CSV: {e}', 'warning')
        return redirect(url_for('variantes_list'))
    return render_template('imagenes_zip.html')

@app.route('/imagenes/reporte_zip')
@login_required
@admin_required
def imagenes_reporte_zip():
    filename = request.args.get('file','')
    if not filename or not filename.startswith('reporte_carga_imagenes_') or not filename.endswith('.csv'):
        flash('Archivo de reporte inválido.', 'danger'); return redirect(url_for('variantes_list'))
    path = os.path.join(BASE_DIR, filename)
    if not os.path.isfile(path):
        flash('No se encontró el reporte solicitado.', 'danger'); return redirect(url_for('variantes_list'))
    return send_file(path, as_attachment=True)

# ---------- Tiendas / Inventario / Movimientos ----------
@app.route('/tiendas')
@login_required
def tiendas_list():
    tiendas = Tienda.query.order_by(Tienda.nombre).all()
    return render_template('tiendas_list.html', tiendas=tiendas)

@app.route('/tiendas/nueva', methods=['POST'])
@login_required
@admin_required
def tiendas_nueva():
    nombre = request.form.get('nombre','').strip()
    direccion = request.form.get('direccion','').strip()
    if not nombre:
        flash('El nombre de la tienda es obligatorio.', 'danger'); return redirect(url_for('tiendas_list'))
    t = Tienda(nombre=nombre, direccion=direccion); db.session.add(t); db.session.commit()
    flash('Tienda creada.', 'success'); return redirect(url_for('tiendas_list'))

@app.route('/tiendas/<int:tienda_id>/eliminar', methods=['POST'])
@login_required
@admin_required
def tiendas_eliminar(tienda_id):
    t = Tienda.query.get_or_404(tienda_id); db.session.delete(t); db.session.commit()
    flash('Tienda eliminada.', 'success'); return redirect(url_for('tiendas_list'))

@app.route('/inventario')
@login_required
def inventario_view():
    tienda_id = request.args.get('tienda_id', type=int)
    tiendas = Tienda.query.order_by(Tienda.nombre).all()
    q = db.session.query(Inventario, ProductoVariante, ProductoBase, Tienda)\
        .join(ProductoVariante, Inventario.variante_id==ProductoVariante.id)\
        .join(ProductoBase, ProductoVariante.base_id==ProductoBase.id)\
        .join(Tienda, Inventario.tienda_id==Tienda.id)
    if tienda_id: q = q.filter(Inventario.tienda_id==tienda_id)
    inventarios = q.order_by(Tienda.nombre, ProductoBase.nombre, ProductoVariante.talla).all()
    return render_template('inventario.html', inventarios=inventarios, tiendas=tiendas, tienda_id=tienda_id)

@app.route('/movimientos')
@login_required
def movimientos_list():
    tipo = request.args.get('tipo')
    tienda_id = request.args.get('tienda_id', type=int)
    variante_id = request.args.get('variante_id', type=int)
    movimientos = Movimiento.query.order_by(Movimiento.fecha.desc())
    if tipo: movimientos = movimientos.filter(Movimiento.tipo==tipo)
    if tienda_id: movimientos = movimientos.filter((Movimiento.origen_tienda_id==tienda_id) | (Movimiento.destino_tienda_id==tienda_id))
    if variante_id: movimientos = movimientos.filter(Movimiento.variante_id==variante_id)
    movimientos = movimientos.all()
    tiendas = Tienda.query.order_by(Tienda.nombre).all()
    variantes = db.session.query(ProductoVariante).order_by(ProductoVariante.id.desc()).all()
    return render_template('movimientos_list.html', movimientos=movimientos, tiendas=tiendas, variantes=variantes, tipo=tipo, tienda_id=tienda_id, variante_id=variante_id)

@app.route('/movimientos/nuevo')
@login_required
def movimientos_nuevo_form():
    tiendas = Tienda.query.order_by(Tienda.nombre).all()
    variantes = db.session.query(ProductoVariante, ProductoBase).join(ProductoBase, ProductoVariante.base_id==ProductoBase.id).order_by(ProductoBase.nombre, ProductoVariante.talla, ProductoVariante.color).all()
    return render_template('movimiento_form.html', tiendas=tiendas, variantes=variantes)

@app.route('/movimientos/nuevo', methods=['POST'])
@login_required
def movimientos_nuevo():
    tipo = request.form.get('tipo')
    variante_id = request.form.get('variante_id', type=int)
    origen_id = request.form.get('origen_tienda_id', type=int)
    destino_id = request.form.get('destino_tienda_id', type=int)
    cantidad = request.form.get('cantidad', type=int)
    nota = request.form.get('nota','').strip()
    if tipo not in ('ENTRADA','SALIDA','TRASLADO'):
        flash('Tipo de movimiento inválido.', 'danger'); return redirect(url_for('movimientos_nuevo_form'))
    if not variante_id or not cantidad or cantidad<=0:
        flash('Variante y cantidad (>0) son obligatorios.', 'danger'); return redirect(url_for('movimientos_nuevo_form'))
    try:
        with db.session.begin():
            if tipo=='ENTRADA':
                if not destino_id: raise ValueError('Selecciona tienda destino para ENTRADA')
                inv_dest = obtener_inventario(destino_id, variante_id); inv_dest.cantidad += cantidad
            elif tipo=='SALIDA':
                if not origen_id: raise ValueError('Selecciona tienda origen para SALIDA')
                inv_ori = obtener_inventario(origen_id, variante_id)
                if inv_ori.cantidad < cantidad: raise ValueError('Stock insuficiente para la salida')
                inv_ori.cantidad -= cantidad
            else:
                if not origen_id or not destino_id or origen_id==destino_id: raise ValueError('TRASLADO requiere origen y destino distintos')
                inv_ori = obtener_inventario(origen_id, variante_id)
                if inv_ori.cantidad < cantidad: raise ValueError('Stock insuficiente para el traslado')
                inv_dest = obtener_inventario(destino_id, variante_id)
                inv_ori.cantidad -= cantidad; inv_dest.cantidad += cantidad
            mov = Movimiento(tipo=tipo, cantidad=cantidad, nota=nota, usuario_nombre=current_user.nombre,
                             variante_id=variante_id, origen_tienda_id=origen_id if tipo!='ENTRADA' else None,
                             destino_tienda_id=destino_id if tipo!='SALIDA' else None)
            db.session.add(mov)
        flash('Movimiento registrado.', 'success')
    except Exception as e:
        db.session.rollback(); flash(f'No se pudo registrar el movimiento: {e}', 'danger')
    return redirect(url_for('movimientos_list'))

# ---------- Helpers ----------

def obtener_inventario(tienda_id, variante_id):
    inv = Inventario.query.filter_by(tienda_id=tienda_id, variante_id=variante_id).first()
    if not inv:
        inv = Inventario(tienda_id=tienda_id, variante_id=variante_id, cantidad=0)
        db.session.add(inv); db.session.flush()
    return inv

# ---------- Exportaciones ----------
@app.route('/exportar_inventario')
@login_required
def exportar_inventario():
    inv = Inventario.query.all()
    data=[]
    for i in inv:
        v=i.variante; b=v.base
        data.append({'Tienda': i.tienda.nombre, 'Producto': b.nombre, 'Marca': b.marca,
                     'Talla': v.talla, 'Color': v.color or '', 'SKU': v.sku or '', 'Cantidad': i.cantidad,
                     'Precio': v.precio or 0})
    df = pd.DataFrame(data); ruta = os.path.join(BASE_DIR, 'export_inventario.xlsx'); df.to_excel(ruta, index=False)
    return send_file(ruta, as_attachment=True)

@app.route('/exportar_movimientos')
@login_required
def exportar_movimientos():
    movs = Movimiento.query.order_by(Movimiento.fecha.desc()).all()
    data=[]
    for m in movs:
        v=m.variante; b=v.base
        data.append({'Fecha': m.fecha.strftime('%Y-%m-%d %H:%M'), 'Tipo': m.tipo, 'Producto': b.nombre,
                     'Marca': b.marca, 'Talla': v.talla, 'Color': v.color or '', 'SKU': v.sku or '',
                     'Origen': m.origen_tienda.nombre if m.origen_tienda else '',
                     'Destino': m.destino_tienda.nombre if m.destino_tienda else '',
                     'Cantidad': m.cantidad, 'Usuario': m.usuario_nombre or '', 'Nota': m.nota or ''})
    df = pd.DataFrame(data); ruta = os.path.join(BASE_DIR, 'export_movimientos.xlsx'); df.to_excel(ruta, index=False)
    return send_file(ruta, as_attachment=True)

# ---------- Inicialización (compatible Flask 3.x) ----------

def setup_db_and_admin():
    db.create_all()
    if not Usuario.query.filter_by(nombre='admin').first():
        admin_pass = os.getenv('ADMIN_PASSWORD') or 'admin123'
        u=Usuario(nombre='admin', password=generate_password_hash(admin_pass), rol='admin')
        db.session.add(u)
    if not Tienda.query.first():
        db.session.add_all([Tienda(nombre='Tienda Centro', direccion='Calle 10 #5-30'), Tienda(nombre='Tienda Norte', direccion='Av. 9 #120-45')])
    if not ProductoBase.query.first():
        b1=ProductoBase(nombre='Tenis Runner X', marca='Speedy', descripcion='Tenis para correr')
        b2=ProductoBase(nombre='Tenis Street Pro', marca='Urban', descripcion='Tenis urbanos')
        db.session.add_all([b1,b2]); db.session.flush()
        db.session.add_all([
            ProductoVariante(base_id=b1.id, talla='40', color='Negro', sku='RUNX-40-NG', precio=299900),
            ProductoVariante(base_id=b1.id, talla='42', color='Azul', sku='RUNX-42-AZ', precio=299900),
            ProductoVariante(base_id=b2.id, talla='41', color='Blanco', sku='URB-41-BL', precio=259900)
        ])
    db.session.commit()

with app.app_context():
    setup_db_and_admin()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)

