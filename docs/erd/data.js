// Data ERD AssetMS - model ringkas (domain Aset & Maintenance)
// 6 entitas. Seluruh data yang masih menjadi bagian satu aset (klasifikasi, foto,
// finansial, komponen, penghapusan) dilebur ke entitas aset, dan jadwal +
// realisasi maintenance digabung menjadi satu entitas maintenance.
// Seluruh nama atribut menggunakan bahasa Indonesia.

const C = {
  master: '#0f766e',
  aset: '#1d4ed8',
  maint: '#b45309',
  damage: '#b91c1c',
  line: '#5b6b80',
  one: '#7c3aed',
  audit: '#9aa8b8',
  text: '#0f172a',
  muted: '#64748b',
  border: '#cbd5e1',
  panel: '#fbfdff',
};

const TW = 232;    // lebar tabel
const HEAD_H = 22; // tinggi header
const ROW_H = 15;  // tinggi baris

// kolom: [nama, tipe, penanda]  penanda: PK | FK | U | ''
const tables = {
  departemen: { color: C.master, cols: [
    ['id_departemen', 'int', 'PK'],
    ['nama', 'varchar(100)', 'U'],
  ]},
  karyawan: { color: C.master, cols: [
    ['id_karyawan', 'int', 'PK'],
    ['nomor_karyawan', 'varchar(20)', 'U'],
    ['nama', 'varchar(150)', ''],
    ['id_departemen', 'int', 'FK'],
    ['jabatan', 'varchar(100)', ''],
  ]},
  lokasi: { color: C.master, cols: [
    ['id_lokasi', 'int', 'PK'],
    ['nama', 'varchar(100)', ''],
    ['id_lokasi_induk', 'int', 'FK'],
    ['jalur_lengkap', 'varchar(500)', ''],
    ['tempat_penyimpanan', 'boolean', ''],
  ]},

  // klasifikasi, foto, finansial, komponen, penghapusan = atribut aset
  aset: { color: C.aset, cols: [
    ['id_aset', 'int', 'PK'],
    ['nomor_po', 'varchar(50)', ''],
    ['nomor_aset', 'varchar(50)', 'U'],
    ['nama_aset', 'varchar(200)', ''],
    ['tanggal_pembelian', 'date', ''],
    ['grup_aset', 'varchar(100)', ''],
    ['kategori_aset', 'varchar(100)', ''],
    ['id_lokasi', 'int', 'FK'],
    ['id_departemen', 'int', 'FK'],
    ['foto', 'varchar(255)', ''],
    ['jenis_foto', 'varchar(20)', ''],
    ['nilai_awal', 'decimal(15,2)', ''],
    ['nilai_residual', 'decimal(15,2)', ''],
    ['umur_ekonomis', 'int', ''],
    ['interval_default', 'int', ''],
    ['satuan_interval_default', 'varchar(10)', ''],
    ['interval_khusus_hari', 'int', ''],
    ['pemakaian_pribadi', 'boolean', ''],
    ['komponen', 'text', ''],
    ['komponen_kritis', 'varchar(200)', ''],
    ['penggantian_terakhir', 'date', ''],
    ['tanggal_penghapusan', 'date', ''],
    ['alasan_penghapusan', 'varchar(20)', ''],
    ['nilai_buku', 'decimal(15,2)', ''],
    ['nilai_jual', 'decimal(15,2)', ''],
    ['status_siklus_hidup', 'varchar(20)', ''],
    ['dibuat_oleh', 'int', 'FK'],
    ['dibuat_pada', 'datetime', ''],
  ]},

  // jadwal + realisasi maintenance dalam satu entitas
  maintenance: { color: C.maint, cols: [
    ['id_maintenance', 'int', 'PK'],
    ['id_aset', 'int', 'FK'],
    ['jenis_maintenance', 'varchar(20)', ''],
    ['tanggal_jadwal', 'date', ''],
    ['status', 'varchar(10)', ''],
    ['tanggal_pelaksanaan', 'date', ''],
    ['deskripsi_pekerjaan', 'text', ''],
    ['hasil', 'varchar(30)', ''],
    ['biaya', 'decimal(12,2)', ''],
    ['catatan', 'text', ''],
    ['id_laporan_kerusakan', 'int', 'FK'],
    ['dilaksanakan_oleh', 'int', 'FK'],
    ['dibuat_pada', 'datetime', ''],
  ]},

  laporan_kerusakan: { color: C.damage, cols: [
    ['id_laporan', 'int', 'PK'],
    ['id_aset', 'int', 'FK'],
    ['dilaporkan_oleh', 'int', 'FK'],
    ['judul', 'varchar(200)', ''],
    ['deskripsi', 'text', ''],
    ['tingkat_keparahan', 'varchar(10)', ''],
    ['foto_bukti', 'varchar(255)', ''],
    ['status_laporan', 'varchar(20)', ''],
    ['ditangani_oleh', 'int', 'FK'],
    ['catatan_ga', 'text', ''],
    ['dilaporkan_pada', 'datetime', ''],
    ['diperbarui_pada', 'datetime', ''],
    ['diselesaikan_pada', 'datetime', ''],
  ]},
};

// posisi kiri-atas tiap tabel (x, y) - 3 kolom
const layout = {
  // kolom 1 - master pendukung
  departemen: { x: 76, y: 96 },
  lokasi: { x: 76, y: 180 },

  // kolom 2 - pelaku + aset
  karyawan: { x: 470, y: 96 },
  aset: { x: 470, y: 236 },

  // kolom 3 - maintenance & kerusakan
  maintenance: { x: 888, y: 236 },
  laporan_kerusakan: { x: 888, y: 473 },
};

const panels = [
  { id: 'master', label: 'MASTER PENDUKUNG', x: 68, y: 70,
    members: ['departemen', 'lokasi'] },
  { id: 'pelaku', label: 'PELAKU / REFERENSI AUDIT', x: 462, y: 70,
    members: ['karyawan'] },
  { id: 'aset', label: 'ASET', x: 462, y: 212,
    members: ['aset'] },
  { id: 'maint', label: 'MAINTENANCE & LAPORAN KERUSAKAN', x: 880, y: 212,
    members: ['maintenance', 'laporan_kerusakan'] },
];

const tableCol = { master: 0, pelaku: 1, aset: 1, maint: 2 };
const colIndex = (t) => tableCol[panels.find((p) => p.members.includes(t)).id];

// relasi: f = [tabel anak, kolom FK], t = tabel induk
// k = 'm' many-to-one | 'a' jejak pelaku (audit)
const edges = [
  { f: ['karyawan', 'id_departemen'], t: 'departemen', k: 'm' },
  { f: ['lokasi', 'id_lokasi_induk'], t: 'lokasi', k: 'm' },

  { f: ['aset', 'id_lokasi'], t: 'lokasi', k: 'm' },
  { f: ['aset', 'id_departemen'], t: 'departemen', k: 'm' },

  { f: ['maintenance', 'id_aset'], t: 'aset', k: 'm' },
  { f: ['laporan_kerusakan', 'id_aset'], t: 'aset', k: 'm' },
  { f: ['maintenance', 'id_laporan_kerusakan'], t: 'laporan_kerusakan', k: 'm' },

  { f: ['aset', 'dibuat_oleh'], t: 'karyawan', k: 'a' },
  { f: ['maintenance', 'dilaksanakan_oleh'], t: 'karyawan', k: 'a' },
  { f: ['laporan_kerusakan', 'dilaporkan_oleh'], t: 'karyawan', k: 'a' },
  { f: ['laporan_kerusakan', 'ditangani_oleh'], t: 'karyawan', k: 'a' },
];

const W = 1160;
const H = 718;

module.exports = { C, TW, HEAD_H, ROW_H, tables, layout, panels, edges, colIndex, W, H };
