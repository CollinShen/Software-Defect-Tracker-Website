from django.shortcuts import render, redirect, get_object_or_404
from django.db import connections
from django.http import JsonResponse
import json
from django.views.decorators.http import require_POST
from .models import Defect
from .forms import ExcelFileForm
import pandas as pd
import openpyxl
import psycopg2
import os

conn = psycopg2.connect(
    dbname=os.environ.get('ST_dbname'),
    host=os.environ.get('ST_host'),
    user=os.environ.get('ST_user'),
    password=os.environ.get('ST_password'),
    port=os.environ.get('ST_port'))

cursor = conn.cursor()

def index(request):
    assigned_defects = Defect.objects.all()
    created_defects = Defect.objects.all()

    context = {
        'assigned_defects': assigned_defects,
        'created_defects': created_defects,
    }
    return render(request, 'SCNtool/index.html', context)

def upload(request):
    if request.method == 'POST':
        form = ExcelFileForm(request.POST, request.FILES)
        if form.is_valid():
            excel_file = request.FILES['excel_file']
            return redirect('SCNtool:index')
    else:
        form = ExcelFileForm()
    return render(request, 'SCNtool/upload.html', {'form': form})

current_table = 'scn_defect_queries'
defect_dict = {}
columnList = []
validationList = []
original_defect_dict = {}
primary_key_column = 'id'

def columnUpdater(request, columnU):
    global current_table, validationList
    validationList.clear()
    if columnU in columnList:
        query = f'SELECT DISTINCT "{columnU}" FROM clearquest.{current_table}'
        cursor.execute(query)
        validationList = [states[0] for states in cursor.fetchall()]
    else:
        print("Error column name not found")
    return JsonResponse({'validation': validationList})

def searchDefectsState(request, column, validation):
    global current_table, defect_dict, original_defect_dict, primary_key_column

    query = f"SELECT * FROM clearquest.{current_table} WHERE \"{column}\" = %s ORDER BY \"{primary_key_column}\" ASC"
    cursor.execute(query, (validation,))
    querieslist = [list(row) for row in cursor.fetchall()]

    return JsonResponse({'querieslist': querieslist, 'columnList': columnList})

def updateTable(request, table_name):
    global current_table, defect_dict, original_defect_dict, columnList, primary_key_column
    try:
        if not table_name.replace('_', '').isalnum():
            return JsonResponse({'status': 'error', 'message': 'Invalid table name'}, status=400)

        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'clearquest' AND table_name = %s
            )
        """, (table_name,))
        table_exists = cursor.fetchone()[0]

        if not table_exists:
            return JsonResponse({'status': 'error', 'message': 'Table does not exist'}, status=400)

        current_table = table_name

        # Get the primary key column
        cursor.execute("""
            SELECT a.attname
            FROM   pg_index i
            JOIN   pg_attribute a ON a.attrelid = i.indrelid
                                 AND a.attnum = ANY(i.indkey)
            WHERE  i.indrelid = %s::regclass
            AND    i.indisprimary;
        """, (f'clearquest.{current_table}',))
        primary_key_result = cursor.fetchone()
        primary_key_column = primary_key_result[0] if primary_key_result else 'id'

        cursor.execute(f"""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'clearquest' AND table_name = %s
            ORDER BY ordinal_position
        """, (current_table,))
        columnList = [row[0] for row in cursor.fetchall()]

        cursor.execute(f"SELECT * FROM clearquest.{current_table} ORDER BY \"{primary_key_column}\" ASC")
        defect_dict.clear()
        for row in cursor:
            k = list(row)
            defect_dict[k[0]] = k[1:]
        original_defect_dict = {k: v[:] for k, v in defect_dict.items()}

        return JsonResponse({'status': 'success', 'columns': columnList, 'primaryKeyColumn': primary_key_column})
    except Exception as e:
        print(f"Error in updateTable: {str(e)}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

def getTables(request):
    cursor.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'clearquest'
    """)
    tables = [row[0] for row in cursor.fetchall()]
    return JsonResponse({'tables': tables})

@require_POST
def saveDefectChanges(request):
    global defect_dict, original_defect_dict, current_table, primary_key_column

    try:
        data = json.loads(request.body)
        row_id = data.pop('row_id', None)

        if not row_id:
            return JsonResponse({'status': 'error', 'message': 'No row_id provided'}, status=400)

        update_fields = []
        values = []
        for key, value in data.items():
            update_fields.append(f"\"{key}\" = %s")
            values.append(value)

        if not update_fields:
            return JsonResponse({'status': 'error', 'message': 'No valid fields to update'}, status=400)

        query = f"""UPDATE clearquest.{current_table} SET {', '.join(update_fields)} WHERE "{primary_key_column}" = %s"""
        values.append(row_id)

        cursor.execute(query, tuple(values))
        rows_affected = cursor.rowcount
        conn.commit()

        if rows_affected == 0:
            return JsonResponse({'status': 'warning', 'message': 'No rows were updated'}, status=200)

        cursor.execute(f"SELECT * FROM clearquest.{current_table} WHERE \"{primary_key_column}\" = %s", (row_id,))
        updated_row = cursor.fetchone()

        if updated_row:
            updated_values = list(updated_row)
            defect_dict[row_id] = updated_values[1:]
            original_defect_dict[row_id] = updated_values[1:]

        return JsonResponse({
            'status': 'success',
            'message': f'{rows_affected} row(s) updated successfully',
            'updated_data': updated_row
        })

    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON data'}, status=400)
    except Exception as e:
        conn.rollback()
        print("Error:", str(e))
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

def defect_detail(request):
    context = {
        'column': columnList,
        'validation': validationList,
        'defect_dict': defect_dict
    }
    return render(request, 'SCNtool/defect_detail.html', context)
