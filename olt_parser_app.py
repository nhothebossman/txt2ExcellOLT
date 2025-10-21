# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import re
import io
import os # To get filename

# --- Regex Patterns ---
# Matches the start of a port section, capturing the port ID (e.g., "0/1/0")
PORT_HEADER_RE = re.compile(r"In port (\d+/\d+/\d+), the total of ONTs are: \d+, online: \d+")
# Matches the header line of the first table (Run State, Last Up/Down Time)
TABLE1_HEADER_RE = re.compile(r"ONT\s+Run\s+Last\s+Last\s+Last")
# Matches the header line of the second table (SN, Type, Distance, Power)
TABLE2_HEADER_RE = re.compile(r"ONT\s+SN\s+Type\s+Distance\s+Rx/Tx power\s+Description")
# Matches a data line in the *first* table
# Groups: 1:ONT_ID, 2:State, 3:UpDateTime, 4:DownDateTime, 5:DownCause
TABLE1_DATA_RE = re.compile(
    r"^\s*(\d+)\s+"                 # 1: ONT ID
    r"(online|offline)\s+"          # 2: Run State
    r"([\d-]{10}\s[\d:]{8}|-)\s+"   # 3: Last Up Time (YYYY-MM-DD HH:MM:SS or -)
    r"([\d-]{10}\s[\d:]{8}|-)\s+"   # 4: Last Down Time (YYYY-MM-DD HH:MM:SS or -)
    r"([\w/-]+|-)\s*$"              # 5: Last Down Cause (word, word/word, or -)
)
# Matches a data line in the *second* table
# Groups: 1:ONT_ID, 2:SN, 3:Type, 4:Distance, 5:Power, 6:Description
TABLE2_DATA_RE = re.compile(
    r"^\s*(\d+)\s+"                 # 1: ONT ID
    r"([0-9A-Fa-f]{16}|-)\s+"       # 2: SN (16 hex chars or -)
    r"(\S+)\s+"                     # 3: Type (non-space chars)
    r"(\d+|-)\s+"                   # 4: Distance (digits or -)
    r"(\S+/-?\S+|-/-)\s+"           # 5: Rx/Tx Power (val/val or -/-)
    r"(.*)\s*$"                     # 6: Description (rest of line)
)

# --- Core Parsing Function ---

def parse_olt_output(text_content, olt_name="Unknown OLT"):
    """
    Parses the Huawei OLT 'display ont info summary' output.

    Args:
        text_content (str): The raw text output from the OLT command.
        olt_name (str): The name of the OLT (usually derived from filename).

    Returns:
        list: A list of dictionaries, where each dictionary represents one ONT's data.
    """
    all_ont_data = []
    current_port = None
    parsing_state = None  # Can be 'table1', 'table2', or None
    port_data_table1 = {} # Stores {ont_id: {data}} for the current port's table 1
    port_data_table2 = {} # Stores {ont_id: {data}} for the current port's table 2

    # --- Derive PoP Name from OLT Name (Example: HWGPON2U-01-PNHHQ -> PNHHQ) ---
    pop_name = "Unknown PoP"
    try:
        parts = olt_name.split('-')
        if len(parts) >= 3:
            # Handle cases like SHVNOC1 correctly
            if parts[-1].upper().endswith("NOC1"):
                 pop_name = parts[-1]
            # Standard case like PNHHQ
            elif len(parts[-1]) >= 5: # Basic check for standard PoP codes
                 pop_name = parts[-1]
            # Fallback if needed
            elif len(parts) >= 4:
                 pop_name = parts[-2] + "-" + parts[-1] # e.g., for longer names

    except Exception:
        pass # Keep default if parsing fails

    lines = text_content.splitlines()

    for line in lines:
        line = line.strip()
        if not line: # Skip empty lines
            continue

        # Check for port header
        port_match = PORT_HEADER_RE.search(line)
        if port_match:
            # --- Process previous port's data before starting new one ---
            if current_port is not None:
                # Combine data for the completed port
                for ont_id_str, t1_data in port_data_table1.items():
                    ont_id = int(ont_id_str) # Ensure ONT ID is integer for consistent keying
                    t2_data = port_data_table2.get(ont_id_str, {}) # Get matching data from table 2

                    # Split DateTimes
                    up_date, up_time = t1_data['UpDateTime'].split() if t1_data['UpDateTime'] != '-' else ('-', '-')
                    down_date, down_time = t1_data['DownDateTime'].split() if t1_data['DownDateTime'] != '-' else ('-', '-')

                    record = {
                        "OLT Name": olt_name,
                        "PON Port": current_port,
                        "ONT ID": ont_id, # Use integer ID
                        "Run State": t1_data['Run State'],
                        "Last UpDate": up_date,
                        "Last UpTime": up_time,
                        "Last DownDate": down_date,
                        "Last DownTime": down_time,
                        "Last DownCause": t1_data['DownCause'],
                        "SN": t2_data.get('SN', 'N/A'),
                        "Type": t2_data.get('Type', 'N/A'),
                        "Distance (m)": t2_data.get('Distance', 'N/A'),
                        "Rx/Tx (dBm) power": t2_data.get('Power', 'N/A'),
                        "Description": t2_data.get('Description', 'N/A'),
                        "PoP": pop_name
                    }
                    all_ont_data.append(record)


            # --- Start new port section ---
            current_port = port_match.group(1)
            parsing_state = None
            port_data_table1 = {}
            port_data_table2 = {}
            #st.write(f"Found port: {current_port}") # Debugging line
            continue # Move to next line after processing header

        # Skip lines if we haven't found a port yet
        if current_port is None:
            continue

        # Check for table headers to change state
        if TABLE1_HEADER_RE.search(line):
            parsing_state = 'table1'
            #st.write("Switched to Table 1 parsing") # Debugging line
            continue
        if TABLE2_HEADER_RE.search(line):
            parsing_state = 'table2'
            #st.write("Switched to Table 2 parsing") # Debugging line
            continue

        # Skip separator lines
        if line.startswith('---'):
            continue

        # --- Parse data lines based on current state ---
        if parsing_state == 'table1':
            data_match = TABLE1_DATA_RE.match(line)
            if data_match:
                ont_id_str = data_match.group(1)
                port_data_table1[ont_id_str] = {
                    "Run State": data_match.group(2),
                    "UpDateTime": data_match.group(3),
                    "DownDateTime": data_match.group(4),
                    "DownCause": data_match.group(5)
                }
                #st.write(f"T1 Match: ID={ont_id_str}, Data={port_data_table1[ont_id_str]}") # Debugging line
        elif parsing_state == 'table2':
            data_match = TABLE2_DATA_RE.match(line)
            if data_match:
                ont_id_str = data_match.group(1)
                ont_type = data_match.group(3) # Get the original type

                # *** NEW: Map numerical types ***
                if ont_type == '1112':
                    ont_type = 'GP1702-4G'
                elif ont_type == '1108':
                    ont_type = 'GP1702-4G-M'
                # *** END NEW ***

                port_data_table2[ont_id_str] = {
                    "SN": data_match.group(2),
                    "Type": ont_type, # Use the potentially modified type
                    "Distance": data_match.group(4),
                    "Power": data_match.group(5),
                    "Description": data_match.group(6).strip() # Clean up trailing spaces
                }
                #st.write(f"T2 Match: ID={ont_id_str}, Data={port_data_table2[ont_id_str]}") # Debugging line

    # --- Process the *last* port's data after the loop ends ---
    if current_port is not None:
         # Combine data for the last processed port
         for ont_id_str, t1_data in port_data_table1.items():
            ont_id = int(ont_id_str)
            t2_data = port_data_table2.get(ont_id_str, {})

            up_date, up_time = t1_data['UpDateTime'].split() if t1_data['UpDateTime'] != '-' else ('-', '-')
            down_date, down_time = t1_data['DownDateTime'].split() if t1_data['DownDateTime'] != '-' else ('-', '-')

            record = {
                "OLT Name": olt_name,
                "PON Port": current_port,
                "ONT ID": ont_id,
                "Run State": t1_data['Run State'],
                "Last UpDate": up_date,
                "Last UpTime": up_time,
                "Last DownDate": down_date,
                "Last DownTime": down_time,
                "Last DownCause": t1_data['DownCause'],
                "SN": t2_data.get('SN', 'N/A'),
                "Type": t2_data.get('Type', 'N/A'),
                "Distance (m)": t2_data.get('Distance', 'N/A'),
                "Rx/Tx (dBm) power": t2_data.get('Power', 'N/A'),
                "Description": t2_data.get('Description', 'N/A'),
                "PoP": pop_name
            }
            all_ont_data.append(record)

    return all_ont_data


# --- Streamlit Application UI ---

st.set_page_config(layout="wide", page_title="OLT Parser")

st.title("📡 Huawei OLT ONT Info Parser")
st.markdown("Upload `display ont info summary` output files (.txt) to extract data into Excel.")

uploaded_files = st.file_uploader(
    "Choose OLT output files (.txt)",
    type="txt",
    accept_multiple_files=True
)

if uploaded_files:
    master_data_list = []
    files_processed = 0
    files_failed = 0

    st.markdown("---")
    progress_bar = st.progress(0)
    status_text = st.empty()

    total_files = len(uploaded_files)

    for i, file in enumerate(uploaded_files):
        try:
            # Derive OLT Name from filename (remove extension)
            olt_name = os.path.splitext(file.name)[0]

            status_text.info(f"Processing file {i+1}/{total_files}: **{file.name}**...")

            # Decode file content
            string_data = file.getvalue().decode("utf-8", errors='ignore') # Added errors='ignore'

            # Parse the data using the core function
            extracted_records = parse_olt_output(string_data, olt_name)

            if extracted_records:
                master_data_list.extend(extracted_records)
                files_processed += 1
                # Only show success message if not too many files, otherwise it floods the UI
                if total_files <= 10:
                     st.success(f"✅ Extracted {len(extracted_records)} ONTs from **{file.name}**")
            else:
                 st.warning(f"⚠️ No valid ONT data found in **{file.name}**")
                 files_failed += 1

        except Exception as e:
            st.error(f"❌ Failed to process **{file.name}**: {e}")
            files_failed += 1

        # Update progress bar
        progress_bar.progress((i + 1) / total_files)

    final_message = f"Processing complete. Files processed: {files_processed}, Files failed/empty: {files_failed}."
    if files_processed > 0:
        status_text.success(final_message + f" Total ONTs extracted: {len(master_data_list)}")
    else:
        status_text.error(final_message)


    # --- Display and Download ---
    if master_data_list:
        df = pd.DataFrame(master_data_list)
        
        # --- Sort by OLT Name, PON Port (numerically), and ONT ID ---
        # Create temporary numerical columns for sorting the PON port parts
        df[['PON_Board', 'PON_Slot', 'PON_PortNum']] = df['PON Port'].str.split('/', expand=True).astype(int)
        
        # Sort using the numerical columns
        df = df.sort_values(by=['OLT Name', 'PON_Board', 'PON_Slot', 'PON_PortNum', 'ONT ID'])
        
        # Drop the temporary sorting columns
        df = df.drop(columns=['PON_Board', 'PON_Slot', 'PON_PortNum'])


        # Ensure correct column order as per user's CSV example
        column_order = [
            "OLT Name", "PON Port", "ONT ID", "Run State",
            "Last UpDate", "Last UpTime", "Last DownDate", "Last DownTime",
            "Last DownCause", "SN", "Type", "Distance (m)",
            "Rx/Tx (dBm) power", "Description", "PoP"
        ]
        # Reorder columns, adding any missing ones (like potentially 'PoP' if filename parsing failed)
        df = df.reindex(columns=column_order, fill_value='N/A')


        st.markdown("---")
        st.subheader("📊 Extracted Data Preview (Sorted)")
        st.dataframe(df, use_container_width=True, height=400) # Added height limit for large datasets

        # Prepare Excel file for download
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='ONT Data')

        excel_data = buffer.getvalue()

        st.markdown("---")
        st.download_button(
            label=f"⬇️ Download {len(master_data_list)} ONTs as Excel (.xlsx)",
            data=excel_data,
            file_name="olt_ont_summary.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary"
        )
    elif files_processed == 0 and files_failed > 0:
         st.error("No data could be extracted from any of the uploaded files.")

else:
    st.info("Upload one or more OLT text files to begin.")