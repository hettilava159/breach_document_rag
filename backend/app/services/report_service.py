import io
import logging
from datetime import datetime
from typing import Dict, Any
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

logger = logging.getLogger("app.report_service")

def generate_audit_pdf(audit_data: Dict[str, Any], doc_filename: str) -> io.BytesIO:
    """
    Generates a beautifully styled, professional PDF report of the contract audit
    using ReportLab and returns it as a bytes buffer.
    """
    logger.info(f"Compiling PDF Audit Report for contract: {doc_filename}")
    
    buffer = io.BytesIO()
    
    # Page setup
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,  # 0.5 inch margins for maximum table width
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )
    
    styles = getSampleStyleSheet()
    
    # Custom Styles (defining colors and typography)
    title_style = ParagraphStyle(
        'ReportTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=22,
        leading=26,
        textColor=colors.HexColor('#1e293b'), # slate 800
        spaceAfter=15
    )
    
    section_heading = ParagraphStyle(
        'SectionHeading',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=18,
        textColor=colors.HexColor('#0f172a'),
        spaceBefore=12,
        spaceAfter=8,
        keepWithNext=True
    )
    
    body_style = ParagraphStyle(
        'BodyTextCustom',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#334155'), # slate 700
    )
    
    meta_style = ParagraphStyle(
        'MetaText',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#64748b'), # slate 500
    )
    
    table_cell_bold = ParagraphStyle(
        'TableCellBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#0f172a'),
    )
    
    table_cell_text = ParagraphStyle(
        'TableCellText',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#334155'),
    )
    
    story = []
    
    # --- PAGE 1: Header ---
    story.append(Paragraph("BREACH Compliance Audit Report", title_style))
    story.append(Paragraph(f"Document Audited: <b>{doc_filename}</b>", body_style))
    story.append(Paragraph(f"Audit Executed on: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}", meta_style))
    story.append(Spacer(1, 15))
    
    # --- Score & Summary Card ---
    score = audit_data.get("overall_score", 100)
    summary_text = audit_data.get("summary", "No summary available.")
    
    # Determine color theme based on score.
    # These bands must match the scoring rubric the LLM is instructed to use in
    # agent_service.py's synthesis prompt (85/60/30 cutoffs) - they previously
    # used different cutoffs (80/50), so a contract could be labeled e.g. "MEDIUM"
    # by the LLM's own summary while this report colored it green/"LOW".
    if score >= 85:
        score_color = colors.HexColor('#16a34a')      # Green 600
        score_text = "LOW RISK - SAFE"
        card_bg = colors.HexColor('#f0fdf4')          # Green 50
    elif score >= 60:
        score_color = colors.HexColor('#ea580c')      # Orange 600
        score_text = "MEDIUM RISK - WARNING"
        card_bg = colors.HexColor('#fff7ed')          # Orange 50
    elif score >= 30:
        score_color = colors.HexColor('#dc2626')      # Red 600
        score_text = "HIGH RISK - DANGER"
        card_bg = colors.HexColor('#fef2f2')          # Red 50
    else:
        score_color = colors.HexColor('#7f1d1d')      # Red 900
        score_text = "CRITICAL RISK - DO NOT SIGN"
        card_bg = colors.HexColor('#fee2e2')          # Red 100
        
    score_p_style = ParagraphStyle(
        'ScoreP',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=28,
        leading=32,
        textColor=score_color,
        alignment=1 # Center
    )
    
    score_label_style = ParagraphStyle(
        'ScoreLabel',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=14,
        textColor=score_color,
        alignment=1 # Center
    )
    
    # Score Widget and Summary Table
    score_cell = [
        Paragraph(f"{score}/100", score_p_style),
        Spacer(1, 4),
        Paragraph(score_text, score_label_style)
    ]
    
    summary_cell = [
        Paragraph("<b>Executive Summary:</b>", table_cell_bold),
        Spacer(1, 6),
        Paragraph(summary_text, body_style)
    ]
    
    card_table_data = [[score_cell, summary_cell]]
    card_table = Table(card_table_data, colWidths=[150, 390]) # Total width = 540 (letter width is 612, -72 margin)
    card_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), card_bg),
        ('ALIGN', (0, 0), (0, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 12),
        ('BOX', (0, 0), (-1, -1), 1.5, score_color),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
    ]))
    
    story.append(card_table)
    story.append(Spacer(1, 20))
    
    # --- Detailed Flagged Risks ---
    story.append(Paragraph("Compliance Risk Details & Remediation Suggestions", section_heading))
    
    risks = audit_data.get("risks", [])
    
    if not risks:
        no_risk_style = ParagraphStyle(
            'NoRiskStyle',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=11,
            leading=14,
            textColor=colors.HexColor('#16a34a')
        )
        story.append(Paragraph("🎉 Excellent! No high or medium risks were flagged in this contract.", no_risk_style))
    else:
        for idx, risk in enumerate(risks):
            category = risk.get("category", "General")
            severity = risk.get("severity", "MEDIUM")
            color_hex = risk.get("severity_color", "yellow")
            clause = risk.get("clause_text", "")
            explanation = risk.get("explanation", "")
            citation = risk.get("citation", "")
            suggestion = risk.get("suggestion", "")
            
            # Map color string to ReportLab color
            if color_hex == "red" or severity.upper() == "HIGH":
                sev_bg = colors.HexColor('#fef2f2')
                sev_border = colors.HexColor('#f87171')
                sev_text_color = colors.HexColor('#b91c1c')
            elif color_hex == "yellow" or severity.upper() == "MEDIUM":
                sev_bg = colors.HexColor('#fff7ed')
                sev_border = colors.HexColor('#fb923c')
                sev_text_color = colors.HexColor('#c2410c')
            else:
                sev_bg = colors.HexColor('#f0fdf4')
                sev_border = colors.HexColor('#4ade80')
                sev_text_color = colors.HexColor('#15803d')
                
            risk_header_style = ParagraphStyle(
                'RiskHeaderStyle',
                parent=styles['Normal'],
                fontName='Helvetica-Bold',
                fontSize=11,
                leading=14,
                textColor=sev_text_color
            )
            
            # Table data layout for each risk
            risk_table_data = [
                [
                    Paragraph(f"Risk #{idx+1}: {category} — Severity: {severity}", risk_header_style),
                    ""
                ],
                [
                    Paragraph("Unfavorable Clause:", table_cell_bold),
                    Paragraph(f"<i>\"{clause}\"</i>", table_cell_text)
                ],
                [
                    Paragraph("Risk Analysis:", table_cell_bold),
                    Paragraph(explanation, table_cell_text)
                ],
                [
                    Paragraph("Legal Precedent / Citation:", table_cell_bold),
                    Paragraph(citation or "N/A", table_cell_text)
                ],
                [
                    Paragraph("Remediation Suggestion:", table_cell_bold),
                    Paragraph(suggestion, table_cell_text)
                ]
            ]
            
            # Write Table Flowable
            r_table = Table(risk_table_data, colWidths=[130, 410])
            r_table.setStyle(TableStyle([
                ('SPAN', (0, 0), (1, 0)), # Span risk title across both columns
                ('BACKGROUND', (0, 0), (-1, 0), sev_bg),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('PADDING', (0, 0), (-1, -1), 8),
                ('BOX', (0, 0), (-1, -1), 1.0, sev_border),
                ('LINEBELOW', (0, 0), (-1, 0), 1.0, sev_border),
                ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ]))
            
            story.append(r_table)
            story.append(Spacer(1, 15))
            
    # --- Footer / Disclaimer ---
    story.append(Spacer(1, 20))
    story.append(Paragraph("<b>Disclaimer:</b> This report is generated autonomously by an AI compliance agent using document RAG technology and the Tavily Search API. It represents semantic risk analysis for guidance purposes and does not constitute formal legal counsel. For critical transactions, please consult with a certified legal advocate.", meta_style))
    
    # Compile
    doc.build(story)
    buffer.seek(0)
    
    logger.info("PDF Audit Report successfully built.")
    return buffer
