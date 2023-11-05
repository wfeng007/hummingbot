'''
Created on 2023年1月22日

@author: wfeng007
'''

import math
import importlib
import sys
import hummingbot.client.settings as settings


def reload_module(moduleFileName):
        """
        重新动态加载依赖的module （脚本更新，依赖的模块也可能更新了。注意依赖的模块尽量不要有状态。）
        Imports the script module based on its name (module file name) 
        """
        module_name = moduleFileName
        module = sys.modules.get(f"{settings.SCRIPT_STRATEGIES_MODULE}.{module_name}")
        if module is not None:
            module = importlib.reload(module)
        else:
            module = importlib.import_module(f".{module_name}", package=settings.SCRIPT_STRATEGIES_MODULE)

        return module

class PriInvTransBySqr:
    '''
    以2次指数作为基础的价格库存变化互算逻辑，即square（Exponential）方式。
    x对应 价格，可以自定义量纲。可以是pct，倍数，或归一化等。
    y对应 库存限定50的变化点，对应50pct
    x^2=y 
    '''
    MAXY=50
    MINY=-50
    MAXX=math.sqrt(MAXY)
    MINX=-math.sqrt(MAXY)
    
    @classmethod
    def _getX(cls,y):
        yI=y
        yI=y if y<=cls.MAXY else cls.MAXY
        yI=yI if yI>=cls.MINY else cls.MINY
        if yI>=0:return math.sqrt(yI)
        if yI<0:return -math.sqrt(-yI)
        pass

    @classmethod
    def _getY(cls,x):
        xI=x
        xI=x if x<=cls.MAXX else cls.MAXX
        xI=xI if xI>=cls.MINX else cls.MINX
        if xI>=0:return pow(xI,2)
        if xI<0:return -pow(-xI,2)
        # if xI==0:return 0
        
    #@todo 考虑做成对象属性
    MAX_PRI_CHG=5#根据具体量纲进行设定，PCT,或具体绝对数,[0,50]
    MIN_PRI_CHG=-MAX_PRI_CHG
    
    # MAX_INV_CHG=50 #这里可以固定50作为库存比例百分比。
    # MIN_INV_CHG=-MAX_INV_CHG
    PRI_COEFFIENT=MAXX/MAX_PRI_CHG #X与PRI 量纲的转换系数，使用为：x=PRI_COEFFIENT*priChg（价格变化）

    @classmethod
    def calcInvChgByPriChg(cls,priChg):
        priChgI=priChg if priChg<=cls.MAX_PRI_CHG else cls.MAX_PRI_CHG
        priChgI=priChgI if priChgI>=cls.MIN_PRI_CHG else cls.MIN_PRI_CHG
        xI=priChgI*cls.PRI_COEFFIENT
        return cls._getY(xI)
    
    @classmethod
    def calcPriChgByInvChg(cls,invChg):
        invChgI=invChg
        # invChgI=invChg if invChg<=cls.MAX_INV_CHG else cls.MAX_INV_CHG
        # invChgI=invChgI if invChgI>=cls.MIN_INV_CHG else cls.MIN_INV_CHG
        xR=cls._getX(invChgI)
        priChgR=xR/cls.PRI_COEFFIENT
        return priChgR
    

        
if __name__ == '__main__':
    print(PriInvTransBySqr._getY(0))
    print(PriInvTransBySqr._getY(-7))
    print(PriInvTransBySqr._getY(7.0710))
    print(PriInvTransBySqr._getX(50))
    print(PriInvTransBySqr._getX(-50))
    print(f"PriInvTransBySqr.calcInvChgByPriChg(50):{PriInvTransBySqr.calcInvChgByPriChg(29)}")
    print(f"PriInvTransBySqr.calcPriChgByInvChg(50):{PriInvTransBySqr.calcPriChgByInvChg(5)}")
    
    pass