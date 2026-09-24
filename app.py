import io
import re
import pandas as pd
import pdfplumber
import streamlit as st

st.set_page_config(page_title="Pajak Document Converter", layout="wide")

# Sidebar Pilihan Dokumen
st.sidebar.title("📌 Pilihan Dokumen")
doc_type = st.sidebar.radio(
    "Pilih jenis dokumen yang ingin di-convert:",
    ["Faktur Pajak Keluaran", "Nota Retur", "Bukti Potong (Bukpot)"],
)

# Header Utama
st.title(f"📄 Converter {doc_type}")


# ==========================================
# 1. PARSER FAKTUR PAJAK KELUARAN (STABLE CORETAX PARSER)
# ==========================================
def parse_faktur(pdf_file):
    items_data = []

    with pdfplumber.open(pdf_file) as pdf:
        full_text = ""
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                full_text += t + "\n"

        # --- 1. KODE/NO FAKTUR, TANGGAL & REFERENSI ---
        no_fp = None
        nsfp_m = re.search(
            r"(?:Kode|Nomor)\s*(?:dan\s*Nomor\s*Seri)?\s*Faktur\s*Pajak\s*:\s*([\d\.\-]+)",
            full_text,
            re.IGNORECASE,
        )
        if nsfp_m:
            no_fp = re.sub(r"\D", "", nsfp_m.group(1))

        tgl_fp = None
        tgl_m = re.search(
            r"(?:KOTA|KAB\.[^\n,]*),\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})",
            full_text,
            re.IGNORECASE,
        )
        if tgl_m:
            tgl_fp = tgl_m.group(1).strip()

        referensi = None
        ref_m = re.search(
            r"\(Referensi[^\)]*\:\s*([^\)]+)\)", full_text, re.IGNORECASE
        )
        if ref_m:
            referensi = ref_m.group(1).strip()

        # --- 2. PEMISAHAN BLOK PENJUAL & PEMBELI ---
        pkp_nama, pkp_alamat, pkp_npwp, pkp_nitku = None, None, None, None
        pem_nama, pem_alamat, pem_npwp, pem_nik, pem_nitku = (
            None,
            None,
            None,
            None,
            None,
        )

        split_pembeli = re.split(
            r"Pembeli\s*Barang\s*Kena\s*Pajak\s*/\s*Penerima\s*Jasa\s*Kena\s*Pajak",
            full_text,
            flags=re.IGNORECASE,
        )

        left_text = ""
        right_text = ""

        if len(split_pembeli) > 1:
            left_text = split_pembeli[0]
            right_text = re.split(
                r"Harga\s*Jual|Penggantian|Uang\s*Muka|Termin",
                split_pembeli[1],
                flags=re.IGNORECASE,
            )[0]
        else:
            left_text = full_text
            right_text = full_text

        # --- PARSING BLOK PENJUAL ---
        p_nm = re.search(r"Nama\s*:\s*([^\n]+)", left_text, re.IGNORECASE)
        if p_nm:
            pkp_nama = p_nm.group(1).strip()

        p_alm = re.search(
            r"Alamat\s*:\s*([\s\S]*?)(?=\n?\s*(?:NPWP|NITKU|#|$))",
            left_text,
            re.IGNORECASE,
        )
        if p_alm:
            pkp_alamat = re.sub(r"\s+", " ", p_alm.group(1)).strip()

        # Tangkap NITKU Penjual dari format #22digit
        p_nitk = re.search(r"#\s*(\d{22})", left_text)
        if p_nitk:
            pkp_nitku = p_nitk.group(1)

        p_npwp = re.search(r"NPWP[^\n:]*:\s*([\d\.\-]+)", left_text, re.IGNORECASE)
        if p_npwp:
            pkp_npwp = re.sub(r"\D", "", p_npwp.group(1))

        # --- PARSING BLOK PEMBELI ---
        b_nm = re.search(r"Nama\s*:\s*([^\n]+)", right_text, re.IGNORECASE)
        if b_nm:
            pem_nama = b_nm.group(1).strip()

        # Ekstraksi Alamat tanpa mengikutkan teks setelah #
        b_alm = re.search(
            r"Alamat\s*:\s*([\s\S]*?)(?=\n?\s*#|\n?\s*(?:NPWP|NIK|NITKU|$))",
            right_text,
            re.IGNORECASE,
        )
        if b_alm:
            pem_alamat = re.sub(r"\s+", " ", b_alm.group(1)).strip()

        # Tangkap NITKU Pembeli dari format #22digit
        b_nitk = re.search(r"#\s*(\d{22})", right_text)
        if b_nitk:
            pem_nitku = b_nitk.group(1)

        # Ekstraksi NPWP
        b_npwp = re.search(r"NPWP[^\n:]*:\s*([\d\.\-]+)", right_text, re.IGNORECASE)
        if b_npwp:
            pem_npwp = re.sub(r"\D", "", b_npwp.group(1))

        # Ekstraksi NIK
        b_nik = re.search(r"NIK[^\n:]*:\s*([\d\.\-]+)", right_text, re.IGNORECASE)
        if b_nik:
            pem_nik = re.sub(r"\D", "", b_nik.group(1))

        if not pem_npwp and not pem_nik:
            b_id = re.search(
                r"(?:ID|Nomor Identitas)[^\n:]*:\s*([\d\.\-]+)",
                right_text,
                re.IGNORECASE,
            )
            if b_id:
                raw_id = re.sub(r"\D", "", b_id.group(1))
                if len(raw_id) == 16 and not raw_id.startswith("0"):
                    pem_nik = raw_id
                else:
                    pem_npwp = raw_id

        # --- 3. EKSTRAKSI TABEL BARANG ---
        raw_items = []
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if not row or len(row) < 4:
                        continue
                    clean_row = [str(c).strip() if c else "" for c in row]
                    if clean_row[0].isdigit():
                        h_str = re.sub(r"[^\d]", "", clean_row[3])
                        if h_str.endswith("00"):
                            h_str = h_str[:-2]
                        raw_items.append({
                            "NO": int(clean_row[0]),
                            "KODE BARANG": clean_row[1] if clean_row[1] else "-",
                            "NAMA BARANG/JASA": re.sub(
                                r"\s+", " ", clean_row[2]
                            ).strip(),
                            "HARGA JUAL": int(h_str) if h_str.isdigit() else 0,
                        })

        # --- 4. BINDING DATA KE DF ---
        if not raw_items:
            items_data.append({
                "NO": 1,
                "KODE DAN NOMOR SERI FP": no_fp,
                "PKP - NAMA": pkp_nama,
                "PKP - ALAMAT": pkp_alamat,
                "PKP - NPWP": pkp_npwp,
                "PKP - NITKU": pkp_nitku,
                "PEMBELI - NAMA": pem_nama,
                "PEMBELI - ALAMAT": pem_alamat,
                "PEMBELI - NPWP": pem_npwp,
                "PEMBELI - NIK": pem_nik,
                "PEMBELI - NITKU": pem_nitku,
                "KODE BARANG": "-",
                "NAMA BARANG/JASA": "-",
                "HARGA JUAL": 0,
                "TANGGAL FP": tgl_fp,
                "REFERENSI": referensi,
            })
        else:
            seen_no = set()
            unique_items = []
            for item in raw_items:
                if item["NO"] not in seen_no:
                    seen_no.add(item["NO"])
                    unique_items.append(item)

            unique_items = sorted(unique_items, key=lambda x: x["NO"])
            for item in unique_items:
                items_data.append({
                    "NO": item["NO"],
                    "KODE DAN NOMOR SERI FP": no_fp,
                    "PKP - NAMA": pkp_nama,
                    "PKP - ALAMAT": pkp_alamat,
                    "PKP - NPWP": pkp_npwp,
                    "PKP - NITKU": pkp_nitku,
                    "PEMBELI - NAMA": pem_nama,
                    "PEMBELI - ALAMAT": pem_alamat,
                    "PEMBELI - NPWP": pem_npwp,
                    "PEMBELI - NIK": pem_nik,
                    "PEMBELI - NITKU": pem_nitku,
                    "KODE BARANG": item["KODE BARANG"],
                    "NAMA BARANG/JASA": item["NAMA BARANG/JASA"],
                    "HARGA JUAL": item["HARGA JUAL"],
                    "TANGGAL FP": tgl_fp,
                    "REFERENSI": referensi,
                })

    return items_data


# ==========================================
# 2. PARSER NOTA RETUR
# ==========================================
def parse_nota_retur(pdf_file):
    data = {
        "Nama PT": None,
        "NPWP PT": None,
        "No Faktur Pajak": None,
        "Tanggal Faktur Pajak": None,
        "No Invoice Retur": None,
        "DPP Retur": 0,
        "PPN Retur": 0,
        "Tanggal Nota Retur": None,
    }

    with pdfplumber.open(pdf_file) as pdf:
        text = "".join([
            page.extract_text() + "\n"
            for page in pdf.pages
            if page.extract_text()
        ])
        lines = [line.strip() for line in text.split("\n") if line.strip()]

        for line in lines:
            if re.search(r"\bPT[\.\s]*NASMOCO", line, re.IGNORECASE):
                data["Nama PT"] = line.strip()
                break

        npwp_match = re.search(
            r"\b\d{2}[\.\s]*\d{3}[\.\s]*\d{3}[\.\s]*\d{1}[\.\s]*[-\.]?[\.\s]*\d{3}[\.\s]*\d{3}\b",
            text,
        )
        if npwp_match:
            data["NPWP PT"] = npwp_match.group(0).strip()

        fp_match = re.search(
            r"(\d{3}\.\d{3}-\d{2}\.\d{8})\s+(\d{2}/\d{2}/\d{4})", text
        )
        if fp_match:
            data["No Faktur Pajak"] = (
                fp_match.group(1).replace(".", "").replace("-", "")
            )
            data["Tanggal Faktur Pajak"] = fp_match.group(2)
        else:
            fp_solo = re.search(r"\d{3}\.\d{3}-\d{2}\.\d{8}", text)
            if fp_solo:
                data["No Faktur Pajak"] = fp_solo.group(0).replace(
                    ".", ""
                ).replace("-", "")

        inv_match = re.search(
            r"\b([A-Z]\d{2}-\d{6}\s*/\s*[A-Z]\d{2}-\d{6})\b", text
        )
        if inv_match:
            data["No Invoice Retur"] = inv_match.group(1)

        dates = re.findall(r"\b\d{2}/\d{2}/\d{4}\b", text)
        if dates:
            if not data["Tanggal Faktur Pajak"]:
                data["Tanggal Faktur Pajak"] = dates[0]
            data["Tanggal Nota Retur"] = dates[-1]

        text_without_dates = re.sub(r"\b\d{2}/\d{2}/\d{4}\b", "", text)
        nominal_matches = re.findall(
            r"\b\d{1,3}(?:\.\d{3})+\b", text_without_dates
        )

        if len(nominal_matches) >= 2:
            dpp_str = nominal_matches[-2].replace(".", "")
            ppn_str = nominal_matches[-1].replace(".", "")

            data["DPP Retur"] = int(dpp_str) if dpp_str.isdigit() else 0
            data["PPN Retur"] = int(ppn_str) if ppn_str.isdigit() else 0

    return [data]


# ==========================================
# 3. PARSER BUKTI POTONG (BUKPOT UNIFIKASI)
# ==========================================
def parse_bukpot(pdf_file):
    data = {
        "NOMOR BUKPOT": None,
        "NPWP / NIK": None,
        "NAMA": None,
        "NITKU": None,
        "JENIS PPH": None,
        "KODE OBJEK PAJAK": None,
        "OBJEK PAJAK": None,
        "DPP": 0,
        "TARIF (%)": 0,
        "PAJAK PENGHASILAN": 0,
        "NOMOR DOKUMEN": None,
        "TANGGAL DOKUMEN": None,
        "NPWP PENERBIT": None,
        "NITKU PENERBIT": None,
        "NAMA PT PENERBIT": None,
        "TANGGAL BUKPOT": None,
        "NAMA PENANDATANGAN": None,
    }

    with pdfplumber.open(pdf_file) as pdf:
        text = "".join([
            page.extract_text() + "\n"
            for page in pdf.pages
            if page.extract_text()
        ])

        no_match = re.search(r"NOMOR\s*\n\s*([A-Z0-9]+)", text, re.IGNORECASE)
        if not no_match:
            no_match = re.search(
                r"\b([A-Z0-9]{8,15})\b(?=\s+\d{2}-\d{4})", text
            )
        if no_match:
            data["NOMOR BUKPOT"] = no_match.group(1).strip()

        npwp_wp = re.search(
            r"A\.1\s*NPWP\s*/\s*NIK\s*:\s*(\d+)", text, re.IGNORECASE
        )
        if npwp_wp:
            data["NPWP / NIK"] = npwp_wp.group(1).strip()

        nama_wp = re.search(r"A\.2\s*NAMA\s*:\s*([^\n]+)", text, re.IGNORECASE)
        if nama_wp:
            data["NAMA"] = nama_wp.group(1).strip()

        nitku_wp = re.search(
            r"A\.3\s*NOMOR IDENTITAS[^\n]*\s*:\s*([^\n]+)", text, re.IGNORECASE
        )
        if nitku_wp:
            data["NITKU"] = nitku_wp.group(1).strip()

        jenis_pph = re.search(
            r"B\.2\s*Jenis PPh\s*:\s*([^\n]+)", text, re.IGNORECASE
        )
        if jenis_pph:
            data["JENIS PPH"] = jenis_pph.group(1).strip()

        kode_obj = re.search(r"(?:B\.3\s*)?(\d{2}-\d{3}-\d{2})", text)
        if kode_obj:
            data["KODE OBJEK PAJAK"] = kode_obj.group(1).strip()

        objek_block = re.search(
            r"\d{2}-\d{3}-\d{2}\s+([\s\S]*?)(?=B\.8|Dokumen Dasar|Surat Tagihan)",
            text,
        )
        if objek_block:
            raw_obj = objek_block.group(1)
            raw_obj = re.sub(r"B\.[567]", "", raw_obj)
            raw_obj = re.sub(r"\b\d{1,3}(?:\.\d{3})+\b", "", raw_obj)
            raw_obj = re.sub(r"\s+", " ", raw_obj).strip()
            data["OBJEK PAJAK"] = re.sub(
                r"\s+\d{1,2}(?:\,\d+)?\s*\d*$", "", raw_obj
            )

        b_area = re.search(
            r"KODE OBJEK PAJAK[\s\S]*?(?=B\.8|Dokumen Dasar)", text
        )
        if b_area:
            b_nominals = re.findall(
                r"\b\d{1,3}(?:\.\d{3})+\b", b_area.group(0)
            )
            if len(b_nominals) >= 2:
                dpp_val = b_nominals[0].replace(".", "")
                pph_val = b_nominals[-1].replace(".", "")
                data["DPP"] = int(dpp_val) if dpp_val.isdigit() else 0
                data["PAJAK PENGHASILAN"] = (
                    int(pph_val) if pph_val.isdigit() else 0
                )

            tarif_m = re.search(r"\b(\d{1,2}(?:\,\d+)?)\s*%", b_area.group(0))
            if not tarif_m:
                tarif_m = re.search(
                    r"\b(\d{1,2})\b(?=\s+\d{1,3}(?:\.\d{3})+)", b_area.group(0)
                )
            if tarif_m:
                data["TARIF (%)"] = tarif_m.group(1).replace(",", ".")

        tgl_doc = re.search(
            r"Tanggal\s*:\s*([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4})", text
        )
        if tgl_doc:
            data["TANGGAL DOKUMEN"] = tgl_doc.group(1).strip()

        no_doc = re.search(
            r"B\.9\s*Nomor Dokumen\s*:\s*([^\n]+)", text, re.IGNORECASE
        )
        if no_doc:
            data["NOMOR DOKUMEN"] = no_doc.group(1).strip()

        npwp_pem = re.search(
            r"C\.1\s*NPWP\s*/\s*NIK\s*:\s*(\d+)", text, re.IGNORECASE
        )
        if npwp_pem:
            data["NPWP PENERBIT"] = npwp_pem.group(1).strip()

        nitku_pem = re.search(
            r"C\.2\s*NOMOR IDENTITAS[^\n]*\s*:\s*([^\n]+)", text, re.IGNORECASE
        )
        if nitku_pem:
            data["NITKU PENERBIT"] = nitku_pem.group(1).strip()

        nama_pem = re.search(
            r"C\.3\s*NAMA PEMOTONG[^\n]*\s*:\s*([^\n]+)", text, re.IGNORECASE
        )
        if nama_pem:
            data["NAMA PT PENERBIT"] = nama_pem.group(1).strip()

        tgl_bukpot = re.search(
            r"C\.4\s*TANGGAL\s*:\s*([^\n]+)", text, re.IGNORECASE
        )
        if tgl_bukpot:
            data["TANGGAL BUKPOT"] = tgl_bukpot.group(1).strip()

        ttd = re.search(
            r"C\.5\s*NAMA PENANDATANGAN\s*:\s*([^\n]+)", text, re.IGNORECASE
        )
        if ttd:
            data["NAMA PENANDATANGAN"] = ttd.group(1).strip()

    return [data]


# ==========================================
# EXECUTION / STREAMLIT UI
# ==========================================

uploaded_files = st.file_uploader(
    f"Upload File PDF ({doc_type})", type=["pdf"], accept_multiple_files=True
)

if uploaded_files:
    all_rows = []
    for pdf_file in uploaded_files:
        try:
            if doc_type == "Faktur Pajak Keluaran":
                rows = parse_faktur(pdf_file)
            elif doc_type == "Nota Retur":
                rows = parse_nota_retur(pdf_file)
            elif doc_type == "Bukti Potong (Bukpot)":
                rows = parse_bukpot(pdf_file)

            all_rows.extend(rows)
        except Exception as e:
            st.error(f"Gagal memproses file {pdf_file.name}: {e}")

    if all_rows:
        df = pd.DataFrame(all_rows)

        # Urutan Kolom Faktur Pajak Keluaran
        if doc_type == "Faktur Pajak Keluaran":
            target_columns = [
                "NO",
                "KODE DAN NOMOR SERI FP",
                "PKP - NAMA",
                "PKP - ALAMAT",
                "PKP - NPWP",
                "PKP - NITKU",
                "PEMBELI - NAMA",
                "PEMBELI - ALAMAT",
                "PEMBELI - NPWP",
                "PEMBELI - NIK",
                "PEMBELI - NITKU",
                "KODE BARANG",
                "NAMA BARANG/JASA",
                "HARGA JUAL",
                "TANGGAL FP",
                "REFERENSI",
            ]
            existing_cols = [c for c in target_columns if c in df.columns]
            df = df[existing_cols]

        st.subheader(f"Hasil Ekstraksi Data ({len(df)} Baris)")

        # Display dengan Formatting
        if doc_type == "Faktur Pajak Keluaran":
            st.dataframe(
                df.style.format({"HARGA JUAL": "{:,.0f}"}),
                use_container_width=True,
            )
        elif doc_type == "Bukti Potong (Bukpot)":
            st.dataframe(
                df.style.format(
                    {"DPP": "{:,.0f}", "PAJAK PENGHASILAN": "{:,.0f}"}
                ),
                use_container_width=True,
            )
        elif doc_type == "Nota Retur":
            st.dataframe(
                df.style.format(
                    {"DPP Retur": "{:,.0f}", "PPN Retur": "{:,.0f}"}
                ),
                use_container_width=True,
            )

        # Export Excel
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Data_Rekap")
        excel_data = output.getvalue()

        st.download_button(
            label="📥 Download Data Excel",
            data=excel_data,
            file_name=f"Hasil_Export_{doc_type.replace(' ', '_')}.xlsx",
            mime=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )