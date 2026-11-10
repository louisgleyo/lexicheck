'use strict';
const $ = (id) => document.getElementById(id);
let data = null;
let currentFilter = 'tous';
const statusNames = {vigoureux: 'En vigueur', alerte: 'Alerte potentielle', ancienne_version: 'Ancienne version', indetermine: 'Indéterminé'};
const statuses = ['alerte','indetermine','ancienne_version','vigoureux'];

function text(node, value) { node.textContent = String(value ?? ''); }
function make(tag, attrs = {}, content = null) {
  const el = document.createElement(tag);
  for (const [k,v] of Object.entries(attrs)) {
    if (k === 'class') el.className = v;
    else if (k === 'href') el.href = v;
    else if (k === 'target') el.target = v;
    else el.setAttribute(k, v);
  }
  if (content !== null) text(el, content);
  return el;
}
function officialUrl(id, nature='') {
  if (/^JORFARTI\d{12}$/.test(id)) return `https://www.legifrance.gouv.fr/jorf/id/${id}`;
  if (/^LEGIARTI\d{12}$/.test(id)) {
    return `https://www.legifrance.gouv.fr/${nature === 'CODE' ? 'codes' : 'loda'}/article_lc/${id}`;
  }
  return 'https://www.legifrance.gouv.fr/';
}
function friendlyDate(iso) {
  if (!iso) return '—';
  const d = new Date(String(iso).slice(0,10) + 'T12:00:00Z');
  if (Number.isNaN(d.valueOf())) return '—';
  return new Intl.DateTimeFormat('fr-FR',{day:'2-digit',month:'short',year:'numeric',timeZone:'UTC'}).format(d);
}
function showError(message) { const el = $('error-box'); text(el, message); el.classList.remove('hidden'); }
function clearError() { $('error-box').classList.add('hidden'); }
async function health() {
  try {
    const resp = await fetch('/api/health');
    const obj = await resp.json();
    const indicator = $('connection');
    indicator.className = 'connection ' + (obj.configured ? 'is-good' : 'is-bad');
    text(indicator, obj.configured ? `PISTE · ${obj.environment === 'sandbox' ? 'Sandbox' : 'Production'} configuré` : 'Identifiants PISTE à configurer');
    indicator.prepend(make('i'));
  } catch {
    text($('connection'), 'Connexion serveur indisponible');
  }
}
function setLoading(on) {
  $('loading').classList.toggle('hidden',!on);
  $('analyze-button').disabled=on;
  text($('button-label'),on ? 'Analyse en cours…' : 'Analyser les renvois');
}
async function submitArticle(value) {
  clearError();
  const input = value.trim();
  if (!/LEGIARTI\d{12}/i.test(input)) return showError("Veuillez saisir un identifiant LEGIARTI valide ou une URL Légifrance contenant cet identifiant.");
  setLoading(true);
  try {
    const resp = await fetch('/api/analyze', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({article:input,mode:document.querySelector('input[name="mode"]:checked')?.value||'actuel'})});
    const obj = await resp.json();
    if (!resp.ok) throw new Error(obj.erreur || 'Erreur de communication avec le serveur.');
    data = obj;
    currentFilter = 'tous';
    $('filter-text').value='';
    $('filter-text').disabled=false;
    document.querySelectorAll('.filter').forEach(x=>x.classList.toggle('active',x.dataset.filter==='tous'));
    render();
  } catch(err) {showError(err.message || String(err));}
  finally{setLoading(false);}
}
function render() {
  if(!data)return;
  const c = data.comptes;
  text($('date-pill'),`Vérification · ${friendlyDate(data.dateAnalyse)} · ${data.mode==='actuel'?'Version en vigueur':'Version fournie'}`);
  text($('stat-total'),data.renvois.length);
  text($('stat-alertes'),c.alerte);
  text($('stat-versions'),c.ancienne_version);
  text($('stat-valides'),c.vigoureux);
  text($('result-count'),`(${data.renvois.length})`);
  $('export-button').disabled=false;
  const src = $('source-card'); src.replaceChildren();
  src.append(make('div',{class:'source-name'},data.article.titre || 'Article analysé'));
  src.append(make('div',{class:'source-id'},data.article.id));
  const tags = make('div',{class:'source-meta'});
  tags.append(make('span',{class:'micro-tag'},data.article.etat || 'État inconnu'));
  tags.append(make('span',{class:'micro-tag'},'N° ' + (data.article.numero || 'non renseigné')));
  src.append(tags);
  const versionLabel = data.mode==='actuel' ? 'Version en vigueur à la date du contrôle' : 'Version historique fournie';
  src.append(make('div',{class:'source-note'},versionLabel));
  if(data.versionChangee) src.append(make('p',{class:'source-switch-note'},`Version de l'URL : ${data.articleDemandee?.id} → version analysée : ${data.article.id}`));
  const srcLink = make('a',{class:'source-link',href:officialUrl(data.article.id,data.sourceNature||'CODE'),target:'_blank',rel:'noopener noreferrer'},'Consulter sur Légifrance ↗');src.append(srcLink);
  renderDocument();
  const issues = [...(data.avertissements||[])];
  if((data.mentionsNonResolues||[]).length)issues.push(`${data.mentionsNonResolues.length} mention(s) de type « article L. 123-1 » sans identifiant résolu, potentiellement à vérifier manuellement : ${data.mentionsNonResolues.slice(0,7).join(' ; ')}${data.mentionsNonResolues.length>7?'…':''}`);
  $('warnings-card').classList.toggle('hidden',issues.length===0);
  $('warnings-list').replaceChildren(...issues.map(x=>make('p',{},x)));
  renderResults();
  if(data.renvois.length) document.querySelector('.workspace').scrollIntoView({behavior:'smooth',block:'start'});
}
function renderDocument() {
  if (!data) return;
  const a = data.article;
  text($('document-parent'), a.titre || 'Texte consolidé');
  text($('document-heading'),`Article ${a.numero || 'sans numéro'}`);
  let dates = a.dateDebut ? `Version du ${friendlyDate(a.dateDebut)}` : 'Version sélectionnée';
  if (a.dateFin && !String(a.dateFin).startsWith('2999')) dates += ` au ${friendlyDate(a.dateFin)}`;
  else dates += ' · En vigueur à la date du contrôle';
  text($('document-dates'),dates);
  const link=$('document-link'); link.href=officialUrl(a.id,data.sourceNature||'CODE'); link.hidden=false;
  const banner=$('version-banner');
  banner.classList.toggle('hidden',!data.versionChangee && data.mode!=='historique');
  if(data.versionChangee) text(banner,`Version ancienne détectée (${data.articleDemandee?.id}). La rédaction affichée est celle en vigueur aujourd'hui (${a.id}).`);
  else if(data.mode==='historique') text(banner,`Mode historique : vous examinez précisément la version ${a.id} fournie. Les statuts des renvois sont évalués aujourd'hui.`);
  const body=$('document-body');
  // HTML autorisé et échappé côté serveur par SafeArticleRenderer (aucun HTML PISTE brut).
  body.innerHTML = data.sourceHtmlAnnote || '<p class="document-placeholder">Le contenu HTML de cet article n’est pas disponible via l’API.</p>';
  text($('text-explanation'),`${data.renvoisSurlignes || 0} occurrence(s) de liens analysés surlignée(s). Cliquez sur une référence pour la consulter sur Légifrance.`);
}

function renderResults() {
  if(!data)return;
  const query = $('filter-text').value.trim().toLocaleLowerCase('fr');
  const rows = data.renvois.filter(r=>(currentFilter==='tous'||r.statut===currentFilter)&&(!query||`${r.id} ${r.titre} ${r.raison} ${r.evidence}`.toLocaleLowerCase('fr').includes(query)));
  const list=$('results-list'); list.replaceChildren();
  if(!rows.length){list.append(make('div',{class:'empty-state'},'Aucun renvoi ne correspond à ce filtre.'));return;}
  for(const r of rows){
    const wrap=make('article',{class:'result-item',id:`reference-${r.id}`});
    const top=make('div',{class:'result-row'});
    const left=make('div');
    left.append(make('div',{class:'result-title'},r.titre || r.id));
    left.append(make('div',{class:'result-id'},r.id));
    top.append(left,make('span',{class:'status-pill '+r.statut},statusNames[r.statut]||r.statut));
    wrap.append(top);
    wrap.append(make('p',{class:'result-reason'},r.raison||''));
    const tags=make('div',{class:'result-tags'});
    if(r.etat)tags.append(make('span',{class:'micro-tag'},'État API : '+r.etat));
    if(r.dateFin)tags.append(make('span',{class:'micro-tag'},'Fin de version : '+friendlyDate(r.dateFin)));
    if(r.sources?.includes('texte'))tags.append(make('span',{class:'micro-tag'},'Dans le texte HTML'));
    if(r.sources?.includes('indexation'))tags.append(make('span',{class:'micro-tag'},'Indexation Légifrance'));
    wrap.append(tags);
    if(r.versionActuelle?.id && r.versionActuelle.id!==r.id){
      const concord=make('div',{class:'concordance'});
      concord.append(make('strong',{},'Version actuellement en vigueur identifiée'));
      const a=make('a',{href:officialUrl(r.versionActuelle.id,r.nature),target:'_blank',rel:'noopener noreferrer'},r.versionActuelle.id+' ↗');concord.append(a);
      wrap.append(concord);
    }
    if(r.concordances?.length){
      const concord=make('div',{class:'concordance'});
      concord.append(make('strong',{},'Concordances possibles · ne constituent pas une validation juridique'));
      r.concordances.forEach(x=>concord.append(make('a',{href:officialUrl(x.id,r.nature),target:'_blank',rel:'noopener noreferrer'},`${x.titre || x.id} ${x.type ? '· '+x.type : ''} ↗`)));
      wrap.append(concord);
    }
    const bottom=make('div',{class:'result-bottom'});
    bottom.append(make('span',{class:'result-id'},r.evidence || 'Référence recensée'));
    bottom.append(make('a',{href:officialUrl(r.id,r.nature),target:'_blank',rel:'noopener noreferrer'},'Voir l’article source ↗'));
    wrap.append(bottom);
    list.append(wrap);
  }
}
function exportCsv() {
  if(!data)return;
  const headers=['id_saisi','id_source_analyse','mode','id_cible','titre_cible','statut','etat_API','date_debut','date_fin','sources','motif','version_actuelle','concordances_possibles'];
  const escapeCSV=v=>'"'+String(v??'').replace(/"/g,'""')+'"';
  const lines=[headers.join(';')];
  for(const r of data.renvois){
    const vals=[data.articleDemandee?.id||data.article.id,data.article.id,data.mode,r.id,r.titre,r.statut,r.etat,r.dateDebut,r.dateFin,(r.sources||[]).join('|'),r.raison,r.versionActuelle?.id||'',(r.concordances||[]).map(x=>x.id).join('|')];
    lines.push(vals.map(escapeCSV).join(';'));
  }
  const blob=new Blob(['\ufeff'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'});
  const url=URL.createObjectURL(blob);
  const a=make('a',{href:url,download:`renvois_${data.article.id}_${data.dateAnalyse}.csv`});
  document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
$('analyze-form').addEventListener('submit',ev=>{ev.preventDefault();submitArticle($('article-input').value);});
$('sample').addEventListener('click',()=>{$('article-input').value='LEGIARTI000006307920';$('article-input').focus();});
$('export-button').addEventListener('click',exportCsv);
$('filter-text').addEventListener('input',renderResults);
$('filters').addEventListener('click',ev=>{const button=ev.target.closest('button[data-filter]');if(!button)return;currentFilter=button.dataset.filter;document.querySelectorAll('.filter').forEach(x=>x.classList.toggle('active',x===button));renderResults();});
health();
