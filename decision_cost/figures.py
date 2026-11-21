"""Cost composition, referral trade-off and sensitivity from the same replay."""
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
from matplotlib.colors import ListedColormap

NAMES = {'raw':'Raw','verified':'Verify all','rule':'Cost rule','logistic':'Logistic',
         'frozen_head':'Frozen + head','tuned_encoder':'Tune last layers'}
COLORS = ['#778695','#148477','#ab5342','#4269aa','#a88343','#754e84']


def render(folder, summaries, grid):
    plt.rcParams.update({'svg.hashsalt':'5017-cost','svg.fonttype':'none','font.size':10,
                         'axes.spines.top':False,'axes.spines.right':False})
    groups=[NAMES[r['policy']] for r in summaries]
    fig,ax=plt.subplots(figsize=(8.2,4.4),layout='constrained')
    left=[0.]*len(groups)
    for key,label,color in zip(('compute_cost','human_cost','error_cost'),('Machine','Human review','Error loss'),COLORS):
        values=[r[key]/r['cases'] for r in summaries]
        ax.barh(groups,values,left=left,label=label,color=color)
        left=[a+b for a,b in zip(left,values)]
    ax.set_xlabel('Scenario cost units per scheduled decision')
    ax.set_title('Expected cost of the historical decisions')
    fig.legend(*ax.get_legend_handles_labels(),loc='outside lower center',ncols=3)
    fig.savefig(folder/'cost.svg',metadata={'Date':None})
    plt.close(fig)
    fig, ax=plt.subplots(figsize=(7.6,4.6),layout='constrained')
    locations={}
    for r in summaries:
        locations.setdefault((r['to_human']/r['cases']*100,r['auto_error']),[]).append(NAMES[r['policy']])
    for (x,y),names in locations.items():
        ax.scatter(x,y,color=COLORS[1])
        ax.annotate('\n'.join(names),(x,y),xytext=(5,3),textcoords='offset points',fontsize=8)
    ax.set_xlim(0,max(x for x,y in locations)+12)
    ax.set_ylim(-0.2,max(y for x,y in locations)+0.8)
    ax.set_yticks(range(max(y for x,y in locations)+1))
    ax.set(xlabel='Share sent to human review (%)',ylabel='Observed wrong automatic actions',
           title='Review workload and automatic errors')
    fig.savefig(folder/'workload.svg',metadata={'Date':None})
    plt.close(fig)
    wages=sorted({r['wage'] for r in grid}); scales=sorted({r['loss_scale'] for r in grid})
    policies=list(NAMES)
    matrix=[]; labels=[]
    for wage in wages:
        line=[]; text=[]
        for scale in scales:
            options=[r for r in grid if r['wage']==wage and r['loss_scale']==scale and r['within_observed_limit']]
            best=min((r['total_cost'] for r in options),default=float('inf'))
            # Group differences smaller than 0.01 unit per cohort as economic ties.
            winners=[r for r in options if r['total_cost']-best<=0.01]
            line.append(policies.index(winners[0]['policy']) if len(winners)==1 else (6 if winners else 7))
            text.append('/'.join(str(policies.index(r['policy'])+1) for r in winners) or '—')
        matrix.append(line); labels.append(text)
    fig,ax=plt.subplots(figsize=(9,4.8),layout='constrained')
    ax.imshow(matrix,origin='lower',aspect='auto',vmin=-0.5,vmax=7.5,cmap=ListedColormap(COLORS+['#687a85','#444444']))
    for i,line in enumerate(labels):
        for j,s in enumerate(line):
            ax.text(j,i,s,ha='center',va='center',color='white',fontsize=9)
    ax.set_xticks(range(len(scales)),scales); ax.set_yticks(range(len(wages)),wages)
    ax.set(xlabel='Error-loss multiplier',ylabel='Cost units per review minute',title='Lowest-cost eligible policies by scenario')
    fig.supxlabel('1 Raw   2 Verify all   3 Cost rule   4 Logistic   5 Frozen + head   6 Tune last layers',fontsize=9)
    fig.savefig(folder/'sensitivity.svg',metadata={'Date':None})
    plt.close(fig)
