"""Rebuild the three-page report and defense notes from measured results only."""
import json
from pathlib import Path
from html import escape
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor, white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from common import LABELS, read_jsonl
from evaluation import claim_label, gold_dev, rationale_present

def pct(x): return '-' if x is None else f'{100*x:.1f}%'
def main():
    run=Path(Path('latest_run.txt').read_text(encoding='utf-8').strip())
    s=json.loads((run/'analysis/summary.json').read_text(encoding='utf-8'))
    errors=json.loads((run/'analysis/error_cases.json').read_text(encoding='utf-8'))
    passport=json.loads(Path('index/passport.json').read_text(encoding='utf-8'))
    verify=json.loads((run/'analysis/verification.json').read_text(encoding='utf-8'))
    categories={'not_found':'Поиск не нашёл свидетельство','not_fitted':'Свидетельство не поместилось',
                'wrong_with_evidence':'Модель ошиблась при наличии свидетельства','noinfo_error':'Ошибка на NOINFO'}
    explanations={'not_found':'Нужного документа нет в top-3. Потеря на этапе поиска.',
        'not_fitted':'Нужный документ найден, но полного эталонного обоснования в промпте нет. Потеря при сборке контекста.',
        'wrong_with_evidence':'Полное эталонное обоснование передано. Ошибка на этапе ответа модели; одного улучшения поиска недостаточно.',
        'noinfo_error':'В разметке нет обоснования, но модель выбрала другую метку или нарушила формат. Это ошибка относительно эталона SciFact; отсутствие разметки не доказывает ложность утверждения.'}
    md=['# Четыре разобранные ошибки\n',f'Запуск: `{run}`. Разбор RAG k=3.\n']
    for j,c in enumerate(errors['cases'],1):
        r=c['result']; g=c['gold']; cat=c['category']
        md += [f"## {j}. ID {r['id']}: {categories[cat]}\n",r['claim']+'\n',
               f"Эталон: **{claim_label(g)}**. Разметка обоснований:\n```json\n{json.dumps(g['evidence'],ensure_ascii=False,indent=2)}\n```",
               'Найдено:\n```json\n'+json.dumps(r['found'],indent=2)+'\n```',
               f"Передано: {r['prompt_tokens']} токенов; документы и исходные номера:\n```json\n{json.dumps(r['transmitted'],indent=2)}\n```"]
        for d in r['documents']:
            md += [f"### Документ {d['doc_id']}: {d['title']}\n"]
            md += [f'[{n}] {text}\n' for n,text in d['sentences']]
        extra=' Метка верна, но ссылка не совпадает с эталонным обоснованием.' if r['status']=='ok' and r['parsed']['label']==claim_label(g) else ''
        md += [f"Сырой ответ:\n```json\n{r['raw_response']}\n```\nСтатус: {r['status']}. Ошибка проверки: {r.get('error','нет')}.\n",explanations[cat]+extra+'\n']
    if errors['missing_categories']: md += ['Не обнаруженные категории: '+', '.join(categories[x] for x in errors['missing_categories'])+'. Вместо них показаны другие реальные ошибки.\n']
    Path('error_cases.md').write_text('\n'.join(md),encoding='utf-8')
    p=s['paired']['llm_to_rag3']; b=s['budget_check']
    worst=min(s['experiment1']['llm_only']['recall'],key=s['experiment1']['llm_only']['recall'].get)
    gold=gold_dev(); rows=read_jsonl(run/'experiment1/results.jsonl')
    case=next((r for r in rows if r['mode']=='rag' and claim_label(gold[r['id']])=='SUPPORT' and r['parsed'] and r['parsed']['label']=='NOINFO'),None)
    actual='Такого ответа в данном запуске нет; ниже приведён способ проверки любого такого случая.'
    if case:
        present=rationale_present(gold[case['id']]['evidence'],case['transmitted'])
        hit=bool(set(gold[case['id']]['evidence']) & {d['doc_id'] for d in case['found']})
        stage='ответ модели' if present else ('сборка контекста' if hit else 'поиск')
        actual=(f"Реальный пример ID {case['id']}: {case['claim']}\n\n"
                f"Эталонные документы и предложения: `{json.dumps(gold[case['id']]['evidence'])}`.\n\n"
                f"Найдено: `{json.dumps(case['found'])}`. Передано: `{json.dumps(case['transmitted'])}`. "
                f"Полное обоснование в промпте: {'да' if present else 'нет'}. Этап ошибки: **{stage}**.")
    defense=f'''# Защита лабораторной №2

## Суть за 30 секунд
Мы проверяли, помогает ли поиск научных статей модели оценивать утверждения. Взяли 5183 аннотации, построили векторный индекс BGE и сравнили Qwen без поиска, с поиском и с готовым эталонным свидетельством. Модель не обучали. Разделили ошибки на поиск, сборку промпта и ответ модели.

## Что получилось
LLM-only: {pct(s['experiment1']['llm_only']['accuracy'])} правильных меток из 90. RAG k=3: {pct(s['experiment1']['rag3']['accuracy'])}. RAG исправил {p['fixed']} ответов и испортил {p['broken']}; изменение {p['delta_pp']:+.1f} процентного пункта.
На одинаковых 60 утверждениях: LLM-only {pct(s['matched60']['llm_only']['accuracy'])}, RAG {pct(s['matched60']['rag3']['accuracy'])}, Oracle {pct(s['matched60']['oracle']['accuracy'])}. Эти числа нельзя смешивать с результатами на 90. Oracle здесь ниже RAG: готовое обоснование не гарантирует верный ответ; кроме того, он получает более короткий текст, чем RAG. Это не гарантированная верхняя граница качества.

## 1. Почему модель сказала NOINFO на SUPPORT?
Проверяем три списка: найден ли эталонный документ; какие его предложения переданы; входит ли целиком хотя бы одно эталонное обоснование. Если документ не найден — виноват поиск. Найден, но обоснование не передано — бюджет контекста. Передано целиком, ответ неверен — модель.

{actual}

## 2. Два нужных документа, один на третьем месте
Hit@3 = 1: хотя бы один найден. Recall@3 = 1/2 = 0,5: найден один из двух. RR@3 = 1/3: первый нужный на третьем месте. При k=1 все три равны 0. MRR — среднее RR по утверждениям.

## 3. Два разных ограничения длины
Кодировщик BGE читает максимум {passport['max_length']} своих токенов при создании вектора. Обрезано {passport['truncated_count']} из 5183 аннотаций ({pct(passport['truncated_fraction'])}). Это видно в index/truncation.jsonl и index/passport.json. Полный текст документов остаётся доступен для промпта.
Бюджет Qwen — 2048 входных токенов на всю инструкцию, утверждение, документы и шаблон диалога. При нехватке места отбрасываются целые предложения с конца очередного документа. Это видно в results.jsonl: found, transmitted, prompt_tokens, prompt. Токенизаторы BGE и Qwen разные; их числа напрямую не сравниваются.

## 4. Почему Recall вырос, а промпт не изменился?
При k=5 поиск возвращает больше документов, но лимит 2048 остаётся прежним. Если первые документы уже заняли бюджет, четвёртый и пятый до модели не доходят. В нашем запуске одинаковый промпт у {len(b['unchanged_prompt_k3_to_k5_ids'])} из 90 утверждений. Recall@3={pct(s['by_k']['3']['recall'])}, Recall@5={pct(s['by_k']['5']['recall'])}. Поиск оценивается до обрезки, ответы — после неё.

## 5. Метка правильная, предложение не обосновывает ответ
Если ссылка формально допустима, Accuracy засчитывает правильную метку. «Верная ссылка» не засчитывается: указанное множество предложений не включает эталонное обоснование того же документа. Если предложение вообще не передавали модели, парсер отвергает ответ: тогда он неверный и для Accuracy.

## 6. Почему нельзя сравнивать Oracle на 60 и RAG на 90?
В Oracle только SUPPORT и CONTRADICT, по 30. В общей выборке дополнительно 30 NOINFO, у которых нет эталонного текста. Состав и сложность разные. Для честного сравнения берём одинаковые 60 ID во всех трёх режимах: это блок matched60 в summary.json и правая часть первого графика.

## 7. Бюджет 1024 вместо 2048
Прогноз записан до расчёта: при том же порядке документов переданных предложений не станет больше, поэтому доля полного обоснования не вырастет.
Получили {b['budget2048_count']}/60 ({pct(b['budget2048_count']/60)}) при 2048 и {b['budget1024_count']}/60 ({pct(b['budget1024_fraction'])}) при 1024. Заново собрали контексты и проверили их по эталону; модель не вызывали. Артефакт: contexts_1024.json. Accuracy для бюджета 1024 не измерялась.

## Что говорить по графикам
1. **01_modes**: слева сравниваются правильные метки LLM-only и RAG на 90; справа все три режима на одинаковых 60. Разница показывает пользу поиска и результат при готовом свидетельстве.
2. **02_retrieval**: X — k=1,3,5; Y — проценты. Hit — нашли хотя бы один нужный документ; Recall — доля найденных нужных документов; MRR — насколько высоко первый нужный; «Обоснование в промпте» — дошёл ли нужный текст целиком. Разрыв Hit и доставки показывает потери при сборке.
3. **03_rag_quality**: X — k; Y — проценты верных меток из 90 и верных ссылок из 60. Это разные знаменатели, поэтому расстояние между линиями не равно числу ошибок цитирования.
4. **MLflow**, запуск experiment2, ось **Step**: accuracy, correct_citation и evidence_in_prompt. Шаг равен k. Значение 0,75 означает 75%. Это измерения одной готовой модели, не график обучения. Конкретные значения ниже.

| k | Accuracy /90 | Верная ссылка /60 | Обоснование в промпте /60 |
|---|---|---|---|
'''
    for k,g in s['by_k'].items(): defense+=f"| {k} | {pct(g['accuracy'])} | {pct(g['citation'])} | {pct(g['evidence_in_prompt'])} |\n"
    defense+='\nПодробные четыре ошибки — в error_cases.md; инструкция запуска и словарь — в README.md.\n'
    Path('defense.md').write_text(defense,encoding='utf-8')
    fontroot=Path('C:/Windows/Fonts')
    pdfmetrics.registerFont(TTFont('Arial',str(fontroot/'arial.ttf')))
    pdfmetrics.registerFont(TTFont('ArialBold',str(fontroot/'arialbd.ttf')))
    pdfmetrics.registerFontFamily('Arial',normal='Arial',bold='ArialBold',italic='Arial',boldItalic='ArialBold')
    normal=ParagraphStyle('body',fontName='Arial',fontSize=9,leading=12,spaceAfter=6,textColor=HexColor('#17243a'))
    small=ParagraphStyle('small',parent=normal,fontSize=7.8,leading=10,spaceAfter=4)
    h=ParagraphStyle('head',parent=normal,fontName='ArialBold',fontSize=14,leading=18,spaceBefore=8,spaceAfter=8)
    title=ParagraphStyle('title',parent=h,fontSize=20,leading=25)
    def para(text,style=normal): return Paragraph(text,style)
    def table(rows,widths):
        t=Table([[para(str(v),small) for v in row] for row in rows],colWidths=widths,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),HexColor('#e8eff8')),('VALIGN',(0,0),(-1,-1),'TOP'),
            ('LINEBELOW',(0,0),(-1,0),.6,HexColor('#9eafc5')),('LINEBELOW',(0,1),(-1,-1),.25,HexColor('#d4dce7')),
            ('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),3)]))
        return t
    story=[para('Лабораторная работа 2',title),para('Базовый RAG: проверка научных утверждений',h),
        para('<b>Цель.</b> Проверить пользу поиска по SciFact и определить, где теряется свидетельство: поиск, сборка контекста или ответ модели.'),
        para(f'<b>Условия.</b> 5183 аннотации; dev 90 (30 на класс), holdout 210 не использован. BGE-small-en-v1.5: 384 числа, 512 токенов, нормированные векторы; обрезано {passport["truncated_count"]} аннотаций ({pct(passport["truncated_fraction"])}). Qwen3-4B-Instruct-2507 Q4_K_M на CPU Ryzen 7 3700X, 8 потоков, llama.cpp b12110; температура 0, seed 42, ответ до 128 токенов. Промпт до 2048 токенов после шаблона; structured outputs и проверка ссылок.'),
        para('<b>Прогноз до запуска.</b> Без поиска труднее CONTRADICT; RAG исправит 10-20 ответов, но может испортить 3-10. Oracle ожидается лучше на одинаковых 60 примерах. При росте k поиск не ухудшится, но качество ответов может ухудшиться из-за бюджета и лишнего текста.'),
        para('1. Без поиска, с поиском и с эталоном',h)]
    table1=[['Режим','Accuracy /90','Recall SUPPORT','Recall CONTRADICT','Recall NOINFO','Ссылка /60']]
    for key,label in [('llm_only','LLM-only'),('rag3','RAG k=3')]:
        g=s['experiment1'][key]; table1.append([label,pct(g['accuracy']),*[pct(g['recall'][l]) for l in LABELS],pct(g['citation']) if key=='rag3' else 'не требуется'])
    story += [table(table1,[76,76,87,87,84,101]),Spacer(1,8),
        para(f'Попарно LLM-only → RAG: исправлено <b>{p["fixed"]}</b>, испорчено <b>{p["broken"]}</b>; изменение Accuracy {p["delta_pp"]:+.1f} п.п. Минимальная полнота LLM-only — у {worst}; прогноз о наиболее трудном классе {'подтвердился' if worst=='CONTRADICT' else 'не подтвердился'}. Ошибки вызова/разбора не исключались; таких ответов во всех 420 вызовах: {verify["invalid_answers"]}.'),
        Image(str(run/'analysis/01_modes.png'),width=500,height=180),
        para(f'Oracle получает одно полное эталонное обоснование, без метки. На тех же 60: LLM-only {pct(s["matched60"]["llm_only"]["accuracy"])}, RAG {pct(s["matched60"]["rag3"]["accuracy"])}, Oracle {pct(s["matched60"]["oracle"]["accuracy"])}. Oracle не является гарантированной верхней границей: он получает только одно обоснование, без остального текста. В этом запуске он уступил RAG; конкретные причины разницы отдельно не изолировались.',small),PageBreak(),
        para('2. Сколько документов передавать',title),
        para('Для k=1,3,5 использованы начала одной top-5 выдачи. Первая таблица: 60 SUPPORT/CONTRADICT, включая средние документы/токены. Accuracy: все 90. k=3 повторно не запускался.')]
    rows=[['k','Hit@k','Recall@k','MRR@k','Обоснование /60','Документов, ср.','Токенов, ср.']]
    for k,g in s['by_k'].items(): rows.append([k,pct(g['hit']),pct(g['recall']),pct(g['rr']),pct(g['evidence_in_prompt']),f"{g['mean_documents']:.2f}",f"{g['mean_tokens']:.1f}"])
    story += [table(rows,[25,65,70,70,95,90,96]),Spacer(1,10)]
    rows=[['k','Accuracy /90','Верная ссылка /60','Исправлено к k=3','Испорчено к k=3']]
    for k,g in s['by_k'].items():
        pair=s['paired'].get(f'rag3_to_rag{k}')
        rows.append([k,pct(g['accuracy']),pct(g['citation']),pair['fixed'] if pair else '-',pair['broken'] if pair else '-'])
    story += [table(rows,[25,100,120,133,133]),Spacer(1,9),
        Image(str(run/'analysis/02_retrieval.png'),width=430,height=221),
        para(f'<b>Интерпретация.</b> При k=3→5 Recall изменился с {pct(s["by_k"]["3"]["recall"])} до {pct(s["by_k"]["5"]["recall"])}, а доставка обоснования — с {pct(s["by_k"]["3"]["evidence_in_prompt"])} до {pct(s["by_k"]["5"]["evidence_in_prompt"])}. У {len(b["unchanged_prompt_k3_to_k5_ids"])} из 90 промпт не изменился: бюджет исчерпан раньше новых документов. Изменение ответа при добавлении текста не доказывает конкретный механизм ошибки; проверяем отдельные случаи.'),
        para(f'<b>Проверка бюджета без генерации.</b> Прогноз: при 1024 токенах доставка не вырастет. Получено {b["budget2048_count"]}/60 при 2048 и {b["budget1024_count"]}/60 при 1024. Сохранены пересобранные контексты; качество ответов при 1024 не измерялось.'),
        para('В MLflow оставлены три метрики: accuracy, correct_citation, evidence_in_prompt; Step = k, значения 0-1. Оформленные графики находятся в analysis. Разные знаменатели Accuracy и ссылок нельзя вычитать как число ошибок.',small),PageBreak(),para('3. Где возникают ошибки',title)]
    for j,c in enumerate(errors['cases'],1):
        r=c['result']; g=c['gold']; found=', '.join(f"{x['doc_id']} ({x['score']:.3f})" for x in r['found'])
        refs='; '.join(f'{d}: {ns}' for d,ns in r['transmitted'].items())
        goldrefs='; '.join(f"{d}: "+' / '.join(str(x['sentences']) for x in rs) for d,rs in g['evidence'].items()) or 'нет'
        block=[para(f'{j}. ID {r["id"]}: {categories[c["category"]]}',ParagraphStyle('casehead',parent=h,fontSize=10,leading=12,spaceBefore=7,spaceAfter=4)),
            para(escape(r['claim'])+f' <b>Эталон: {claim_label(g)}.</b>',small),
            para('Нужно: '+escape(goldrefs)+'. Найдено (score): '+escape(found)+'.',small),
            para('Переданы предложения: '+escape(refs)+f'. Всего {r["prompt_tokens"]} токенов.',small),
            para('Сырой ответ: '+escape(str(r['raw_response']))+'. '+explanations[c['category']]+(' Метка верна, но ссылка не совпадает с эталонным обоснованием.' if r['status']=='ok' and r['parsed']['label']==claim_label(g) else ''),small)]
        story.append(KeepTogether(block))
    if errors['missing_categories']: story.append(para('Не встретились: '+', '.join(categories[x] for x in errors['missing_categories'])+'. Показаны другие реальные ошибки.',small))
    best=max(s['by_k'],key=lambda k:s['by_k'][k]['accuracy'])
    story += [Spacer(1,5),para(f'<b>Вывод.</b> Лучший результат среди проверенных k: k={best}, Accuracy {pct(s["by_k"][best]["accuracy"])}. Найти документ, передать обоснование и правильно ответить — три разные проверки. Вывод ограничен данным dev и одной моделью; holdout сохранён для ЛР3.',small),
        para('<b>Воспроизводимость и кодогенерация.</b> Код подготовлен с помощью Codex. Проверены тесты метрик/парсера, близость одинаковых текстов, лимиты и совпадение токенов с usage сервера. Источник SciFact, SHA-256, разбиение и паспорт индекса сохранены. Полные тексты четырёх случаев — error_cases.md; ответы к защите — defense.md. Исходники: experiment.py, evaluation.py, rag.py, build_index.py, analyze.py, make_report.py.',small),
        para(f'Источники: задание ЛР2; официальный SciFact (allenai/scifact); карточка BAAI/bge-small-en-v1.5. Запуск {run.name}; ревизия BGE {passport["revision"][:12]}; полный хеш матрицы и ревизия в index/passport.json.',small)]
    def footer(c,doc):
        c.setFont('Arial',8); c.setFillColor(HexColor('#66758c')); c.drawString(42,25,'ЛР2 · SciFact · воспроизводимые измерения'); c.drawRightString(553,25,str(doc.page))
    doc=SimpleDocTemplate('report.pdf',pagesize=(595.28,841.89),rightMargin=42,leftMargin=42,topMargin=34,bottomMargin=42,title='ЛР2. Базовый RAG на SciFact',author='Лабораторная работа')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    print('Created report.pdf, defense.md, error_cases.md')

if __name__=='__main__': main()
