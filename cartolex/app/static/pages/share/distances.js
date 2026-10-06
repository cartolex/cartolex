// SPDX-License-Identifier: MIT
/**
 * « Export distances »: the nearest of each, every pair (the full similarity matrix) or the
 * vectors, of the people on the map (every one, or those the map's filters keep) or of the
 * organisations of one level, measured in the space the themes are drawn from. What would be
 * written (how many, its format and size) is read from the server as the options change
 * (`POST /api/share/exports` with `plan`); a matrix above ten million cells says its size and
 * waits for « Write it anyway ». People are named or given pseudonyms, as asked each time.
 * The file is written by a job into the exports (the share screen lists them).
 */
import { html, useEffect, useState } from '../../core/preact.js';
import { locale, t } from '../../core/i18n.js';
import {
  Button, Checkbox, Dialog, ErrorCard, FormField, Icon, Input, Select,
} from '../../components/index.js';
import { levelLabel } from '../map/model.js';

const KINDS = ['neighbours', 'similarity', 'vectors'];

/**
 * @param {object} props
 * @param {object} props.ctx the page's context
 * @param {boolean} props.open
 * @param {Function} props.onClose
 * @param {(job: object) => void} props.onStarted the job started
 * @param {string[]|null} [props.shown] the people the map's filters keep (null: no filter)
 */
export function DistancesDialog({ ctx, open, onClose, onStarted, shown = null }) {
  const [kind, setKind] = useState('neighbours');
  const [of, setOf] = useState('person');
  const [level, setLevel] = useState('');
  const [k, setK] = useState('10');
  const [only, setOnly] = useState(true);
  const [names, setNames] = useState('');
  const [confirm, setConfirm] = useState(false);
  const [plan, setPlan] = useState(null);
  const [error, setError] = useState(null);
  const [starting, setStarting] = useState(false);
  const kk = Math.max(1, Math.min(100, Number.parseInt(k, 10) || 10));
  const ids = of === 'person' && shown && only ? shown : null;
  const body = { kind, of, k: kk, ...(level ? { level } : {}), ...(ids ? { ids } : {}) };

  useEffect(() => {
    if (!open) return undefined;
    setConfirm(false);
    const timer = setTimeout(() => {
      ctx.api.post('/api/share/exports', { ...body, plan: true }).then((r) => {
        if (r.ok) {
          setPlan(r.data.plan);
          setError(null);
        } else setError(r.error);
      });
    }, 200);
    return () => clearTimeout(timer);
  }, [open, kind, of, level, kk, ids ? ids.length : -1]);
  useEffect(() => {
    if (!open) {
      setPlan(null);
      setError(null);
    }
  }, [open]);

  const start = async () => {
    setStarting(true);
    const r = await ctx.api.post('/api/share/exports', {
      ...body, ...(of === 'person' ? { names } : {}), confirm,
    });
    setStarting(false);
    if (!r.ok) {
      setError(r.error);
      return;
    }
    onStarted(r.data.job);
    onClose();
  };
  const levels = (plan && plan.levels) || [];
  const needNames = of === 'person' && !names;
  const blocked = !plan || needNames || (plan.confirm && !confirm) || plan.count === 0;
  return html`<${Dialog} open=${open} onClose=${onClose} title=${t('distances.title')} size="l"
    description=${t('distances.lead')}
    footer=${html`<${Button} variant="ghost" onClick=${onClose}>${t('common.cancel')}<//>
      <${Button} variant="primary" loading=${starting} disabled=${blocked} onClick=${start}>
        ${t('distances.start')}<//>`}>
    <div class="cx-share-form cx-distances-form">
      <${FormField} label=${t('distances.kind')}>
        ${(f) => html`<${Select} ...${f} value=${kind} onChange=${(e) => setKind(e.currentTarget.value)}
          options=${KINDS.map((v) => ({ value: v, label: t(`distances.kind.${v}`) }))} />`}<//>
      <${FormField} label=${t('distances.of')}>
        ${(f) => html`<${Select} ...${f} value=${of} onChange=${(e) => setOf(e.currentTarget.value)}
          options=${[{ value: 'person', label: t('distances.of.person') },
            { value: 'organisation', label: t('distances.of.organisation') }]} />`}<//>
      ${of === 'organisation' && levels.length > 1 ? html`<${FormField} label=${t('distances.level')}>
        ${(f) => html`<${Select} ...${f} value=${level || levels[0].id}
          onChange=${(e) => setLevel(e.currentTarget.value)}
          options=${levels.map((lv) => ({ value: lv.id, label: t('distances.level.option',
            { name: levelLabel(lv, locale.value), count: lv.count }) }))} />`}<//>` : null}
      ${kind === 'neighbours' ? html`<${FormField} label=${t('distances.k')}>
        ${(f) => html`<${Input} ...${f} type="number" min="1" max="100" value=${k}
          onInput=${(e) => setK(e.currentTarget.value)} />`}<//>` : null}
      ${of === 'person' ? html`<${FormField} label=${t('distances.names')} help=${t('distances.names.help')}
        required error=${error && error.code === 'export_names_question' ? t('distances.names.required') : ''}>
        ${(f) => html`<${Select} ...${f} value=${names} onChange=${(e) => setNames(e.currentTarget.value)}
          options=${[{ value: '', label: t('distances.names.choose'), disabled: true },
            { value: 'pseudonyms', label: t('distances.names.pseudonyms') },
            { value: 'names', label: t('distances.names.names') }]} />`}<//>` : null}
    </div>
    ${of === 'person' && shown ? html`<${Checkbox} checked=${only} onChange=${(e) => setOnly(e.currentTarget.checked)}
      label=${t('distances.only', { count: shown.length })} />` : null}
    ${plan ? html`<p class="cx-share__note" role="status">${t('distances.plan', {
      count: plan.count, of: plan.of, cells: plan.cells, kind: plan.kind,
      format: plan.format.toUpperCase(), size: plan.size })}</p>` : html`<p class="cx-share__note" aria-busy="true">
      ${t('common.loading')}</p>`}
    ${plan && plan.confirm ? html`<div class="cx-distances-warning" role="note">
      <${Icon} name="warning" />
      <p><strong>${t('toast.kind.warning')}</strong>${' '}${t('distances.large', {
        cells: plan.cells, size: plan.size })}</p>
      <${Checkbox} checked=${confirm} onChange=${(e) => setConfirm(e.currentTarget.checked)}
        label=${t('distances.large.confirm')} />
    </div>` : null}
    ${error && error.code !== 'export_names_question' ? html`<${ErrorCard} error=${error} compact />` : null}
    <p class="cx-share__note">${t('distances.formats', { cells: 10000000 })}</p>
  <//>`;
}
