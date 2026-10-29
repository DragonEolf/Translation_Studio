"""Bounded dynamic programming for compatible paragraph layout candidates.

Text and style ownership are immutable. Font scales and vertical allocations are
chosen jointly using exact shaped measurements, inside one obstacle-free zone.
"""
import math
import pymupdf as fitz
from app.services.cell_layout import CellLayout


class ColumnFlow(CellLayout):
    def __init__(self, fonts, min_size=6, max_measurements=96):
        super().__init__(fonts,min_size)
        self.max_measurements=max_measurements
        self.measurements=0
        self.cache={}

    @staticmethod
    def eligible(entry):
        block,translation,layout=entry
        policy=block.style.get('layout_policy',{})
        return (policy.get('decision')=='reflow' and policy.get('role')=='prose'
                and block.style.get('rich_text') and translation.metadata_json.get('rich_text',{}).get('runs')
                and layout.fit_status in {'fitted','overflow'})

    def measure(self, entry, bounds, scale):
        block,translation,_=entry
        # Translations are fixed for the lifetime of this pass. The ID is safe as
        # part of a local-only cache key; no cross-revision cache is retained.
        key=(block.id,round(bounds.width,4),round(scale,5))
        if key not in self.cache:
            if self.measurements>=self.max_measurements:
                return None
            self.measurements+=1
            self.cache[key]=self._measure([entry],bounds,scale)
        result=self.cache[key]
        return result[0][0] if result else None

    @staticmethod
    def source_rect(block):
        return fitz.Rect(block.style['layout_policy']['source_bbox'])

    def solve(self, group, bounds):
        options=[]
        for entry in group:
            block,_,layout=entry
            source=float(block.style['size'])
            current=layout.font_size/source
            minimum=max(min(self.min_size,source)/source,current*.9 if layout.fit_status=='fitted' else 0)
            scales=sorted({1.,current,*[i/20 for i in range(12,20)]},reverse=True)
            choices=[]
            for scale in scales:
                if scale<minimum-.00001 or scale>1:continue
                height=self.measure(entry,bounds,scale)
                if height is not None and height<=bounds.height:
                    choices.append((scale,height))
            if not choices:return None
            options.append(choices)
        # State: used height and last font scale. Bucket only the lookup key;
        # every retained path still carries its exact height for feasibility.
        states=[(0.,None,0.,[])]
        for index,(entry,choices) in enumerate(zip(group,options)):
            block=entry[0]; source=self.source_rect(block);size=float(block.style['size'])
            gap=0 if index==0 else max(2,min(size*.5,source.y0-self.source_rect(group[index-1][0]).y1))
            best={}
            for used,previous,cost,path in states:
                y=bounds.y0+used+gap
                displacement=(y-source.y0)/size
                # Do not solve one local paragraph by displacing distant content.
                if abs(y-source.y0)>max(source.height*.5,size*3):continue
                for scale,height in choices:
                    end=used+gap+height
                    if end>bounds.height:continue
                    score=cost+12*math.log(scale)**2+.02*displacement**2
                    if previous is not None:score+=24*math.log(scale/previous)**2
                    key=(math.ceil(end*2),round(scale,5))
                    candidate=(end,scale,score,path+[(y,height,scale)])
                    if key not in best or score<best[key][2]:best[key]=candidate
            if not best:return None
            states=sorted(best.values(),key=lambda s:(s[2],s[0]))[:128]
        best=min(states,key=lambda s:(s[2],s[0]))
        return best[2],best[3]

    def apply(self, entries, blocks, page, graphics=()):
        groups=[]
        for entry in sorted(entries,key=lambda e:e[0].reading_order):
            if not self.eligible(entry):
                groups.append([]);continue
            block=entry[0];rect=self.source_rect(block);size=block.style['size']
            previous=groups[-1][-1][0] if groups and groups[-1] else None
            other=self.source_rect(previous) if previous else None
            if (other is None or abs(rect.x0-other.x0)>size*.5
                    or rect.y0-other.y1>size*2 or rect.y0<other.y0
                    or abs(block.style['size']-previous.style['size'])>size*.15):
                groups.append([])
            groups[-1].append(entry)
        changed=[]
        for group in groups:
            if len(group)<2:continue
            identities={b.id for b,_,_ in group}
            bounds=fitz.Rect(group[0][0].bbox)
            for b,_,_ in group[1:]:bounds|=fitz.Rect(b.bbox)
            sources=[self.source_rect(b) for b,_,_ in group]
            obstacles=[fitz.Rect(b.bbox) for b in blocks if b.id not in identities]
            for rect in map(fitz.Rect,graphics):
                # A backdrop or an owned inline decoration already lies behind
                # source text. A rule/image between paragraphs remains a barrier.
                if any(s.contains(rect) for s in sources):continue
                if sum((rect&s).get_area()>1 for s in sources)>=2:continue
                obstacles.append(rect+(-.25,-.25,.25,.25))
            if any((bounds&r).get_area()>1 for r in obstacles):continue
            proposal=self.solve(group,bounds)
            if proposal is None:continue
            cost,placements=proposal
            previous=None;prior_cost=0.
            for block,_,layout in group:
                scale=max(.001,layout.font_size/block.style['size'])
                prior_cost+=12*math.log(scale)**2+.02*((layout.bbox[1]-self.source_rect(block).y0)/block.style['size'])**2
                if previous is not None:prior_cost+=24*math.log(scale/previous)**2
                previous=scale
            if all(l.fit_status=='fitted' for _,_,l in group) and cost>=prior_cost-.00001:
                continue
            for (block,translation,layout),(y,height,scale) in zip(group,placements):
                box=[bounds.x0,y,bounds.x1,y+height]
                if not fitz.Rect(0,0,page.width,page.height).contains(fitz.Rect(box)):
                    raise ValueError('Column flow exceeded its page zone')
                layout.bbox=box
                layout.font_size=block.style['size']*scale
                layout.fit_status='fitted'
                layout.line_breaks=translation.text.splitlines()
                changed.append(block.id)
        return {'changed_blocks':changed,'measurements':self.measurements,
                'measurement_budget':self.max_measurements,'solver':'bounded_dynamic_programming'}
