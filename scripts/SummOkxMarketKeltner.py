'''
Created on 2023年11月11日

@author: wfeng007
'''
from typing import Any, Dict, List, Set
import importlib
import sys
import logging
from decimal import Decimal
import pandas 
import pandas_ta as ta 



import hummingbot.client.settings as settings
from hummingbot.strategy.order_tracker import OrderTracker
from hummingbot.logger import HummingbotLogger

from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.core.data_type.common import PriceType, OrderType, TradeType
from hummingbot.core.data_type.limit_order import LimitOrder
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import OrderFilledEvent, BuyOrderCreatedEvent, \
    SellOrderCreatedEvent, OrderCancelledEvent

from . import summ_hbot_script_utilz as summ_scri_util
from . import summ_hbot_okx_utilz as summ_okx_util #summUtilz 

from . import SummOkxMarketAbs as SummOkxMarketAbs_m
from .SummOkxMarketAbs import Context 


# SummOkxMarketAbs_m=summ_scri_util.reload_module("SummOkxMarket")#重新加载，热加载
summ_okx_util=summ_scri_util.reload_module("summ_hbot_okx_utilz")#重新加载，热加载

class SummOkxMarketKeltner(SummOkxMarketAbs_m.SummOkxMarketAbs):
    '''
    Summ Keltner
    '''

    def __init__(self,connectors: Dict[str, ConnectorBase]):
        '''
        初始化
        '''
        # SummOkxMarketAbs_m=summ_scri_util.reload_module("SummOkxMarket")#重新加载，热加载
        super().__init__(connectors)
        
    '''
        # 初始化对象时执行
        #__init__(self):
        #self.bollingInitialize(self)
        
        #on_stick(self)时：
        #
        # 策略主干实现：继承本类代码实现3个方法 
        # 
        # 策略-量化分析
        #
        self.strategyDataParse(context)
        #
        # 策略-入场信号识别与计算下单
        #
        self.strategyEntrySignalAndProposeOrder(context)
        #
        # 策略-离场信号识别与计算下单
        #
        self.strategyExitSignalAndProposeOrder(context)
        #
        
        # 策略-实际下单：
        self.strategyPlaceOrder(context)

    '''
    #


    def strategyInitialize(self):
        '''
        策略脚本初始化：建立肯特纳通道数据；
        '''
        #交易量
        self.order_amount = 0.01 
        
        self.position_mark=0 #仓位标志
        self.isCoolingDown=False #

        #生成keltner数据结构
        ktnDFColumns = ["timestamp", "upper", "sma", "lower","atr"]
        self.ktnDF:pandas.DataFrame=pandas.DataFrame(columns=ktnDFColumns)


        pass


    def strategyDataParse(self,context:Context):
        '''
        肯特钠策略的数据解析
        '''
        #
        # atr计算与ktn通道计算
        # closes = [float(closeP) for closeP in context.nowCandlesDf['close']]
        # context.candleCloseLs=closes
        # upper_band, sma, lower_band=self.calc_bollinger_bands(closes,n=20 if len(closes)>= 20 else len(closes) )
        # context.upper_band=upper_band
        # context.sma=sma
        # context.lower_band=lower_band
        context.nowCandlesDf # @Fixme 用ta好像要倒转df顺序nowCandlesDf 应该最新的在最后
        atr1=ta.atr(high=context.nowCandlesDf.highest,low=context.nowCandlesDf.lowest,close=context.nowCandlesDf.close,length=14)
        self.ktnDF['timestamp']=context.nowCandlesDf['timestamp']
        self.ktnDF['atr']=atr1
        self.ktnDF['ktn_high']=self.ktnDF['sma']+1.5*atr1
        self.ktnDF['ktn_low']=self.ktnDF['sma']-1.5*atr1

        #根据是否candle新周期执行；
        # if nowTs not in self.bollingerDf['timestamp'].values:
        if context.isNewCandlePeriod:
            # 如果不存在,判单为candle新周期了...
            ...
        else:
            # 如果不是新bar周期
            ...
        
        context.ktnDF=self.ktnDF
        # self.log_with_clock(logging.INFO, f"bolling is ok!") 调试

        pass

    def strategyEntrySignalAndProposeOrder(self,context:Context):
        '''
            入场信号与入场下单
            使用context.refPrice
        '''
        context.refPrice
        ...

    def strategyExitSignalAndProposeOrder(self,context:Context):
        '''
            离场信号与离场下单
        '''
        ...
    ###
        

    # 最后的3个参数之后抽象放入通用参数或放入属性df，closes最好直接用candles
    def getBollingEntrySignal(self,ref_price:Decimal,closes:List,upper_band, sma, lower_band): 
        '''
            根据信息比如candle或candle的结束价计算入场信号，确定是否要入场；
            return  0 表示没有建仓信号
                    1/>0 表示建多仓信号
                    -1/<0 表示建空仓信号
            
            这里使用了rocThre来确定是否要建仓的另个基本过滤；前某个周期的close与当前参考价格比较来确定入场；
            @TODO 之后可以考虑对rocThre的计算可以动态比较，而不是rocThre>0 的0才是动态阈值/门限；
        '''

        #
        #入场信号
        #
        #
        rocLen=2 #入场过滤器参数
        #@TODO 入场过滤器计算，之后替换为更合理的入场信号；
        rocThre=float(ref_price)-closes[-rocLen] #计算过滤器值，正负方向，大小为强度；

        #
        #开仓逻辑与信号处理 
        #
        #无多头持仓，且roc过滤器为正，突破上线，开多仓；不在冷却中；
        if self.position_mark == 0 and not self.isCoolingDown and rocThre>0 and ref_price>upper_band:
            return 1
        #无空头仓，且roc过滤器为负，突破下线，开空仓；不在冷却中；
        elif self.position_mark == 0 and not self.isCoolingDown and rocThre<0 and ref_price<lower_band:
            return -1
        
        return 0

    def _createOrderCandi(self,order_side:TradeType,price:Decimal=None):
        return OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,\
                order_side=order_side, amount=Decimal(self.order_amount), price=price)
    
    def format_status(self)->str:
        '''
        展示BB数据，以及布林强盗算法中的离场移动均线；
        '''
        lines = []
        try:
            if not self.ready_to_trade:
                return "Market connectors are not ready."
            # warning_lines = []
            # warning_lines.extend(self.network_warning(self.get_market_trading_pair_tuples()))
            
            lines.extend([f"策略基本信息：{self.STRATEGY_INFO} "])
            lines.extend([" "])
            lines.extend([f"是否有跟踪订单isOrderTracked:{self.orderTracking.isOrderTracked}"])
            try:
                trackedDf = self.orderTracking.getTrackedOrdersDf()
                lines.extend(["策略层跟踪订单StrategyTrackedOrders:"] \
                            + ["    " + line for line in trackedDf.to_string(index=False).split("\n")])
            except ValueError:
                lines.extend(["没有策略层订单 No Strategy Orders."])
            lines.extend([" "])
            lines.extend(["仓位情况标志,position_mark:"+str(self.position_mark)])
            lines.extend(["是否冷却中,isCoolingDown:"+str(self.isCoolingDown)])
            lines.extend([" "])

            bolDf=self.bollingerDf.copy()
            bolDf=bolDf.iloc[-3:]
            lines.extend(["bolling信息[1m-bolling]-3:"] + ["    " + line for line in bolDf.to_string(index=False).split("\n")])
            lines.extend([" "])
            
            candlesDf=self.currentCandlesDf
            candlesDf=candlesDf.iloc[0:3]
            lines.extend(["1分钟k线信息[1m-kline]:"] + ["    " + line for line in candlesDf.to_string(index=False).split("\n")])
            lines.extend([" "])
            # return "\n".join(lines)
        except Exception as e :
            self.log_with_clock(logging.ERROR, f"except on on_tick:{str(e)}")
            self.logger().exception(f"except on on_tick:{e}")
        finally:
            return "\n".join(lines)
            pass
        pass
        
    
    
    #
    #执行后回调
    #
    def did_fill_order(self, event: OrderFilledEvent):
        '''
        完成订单交易完成后回调
        日志与提示界面通知。
        '''
        msg = (f"{event.trade_type.name} {round(event.amount, 2)} {event.trading_pair} {self.exchange} at {round(event.price, 2)}")
        self.log_with_clock(logging.INFO, msg)
        self.notify_hb_app_with_timestamp(msg)


        if self.position_mark == 0 :# 开仓，并标记 
            if event.trade_type == TradeType.BUY: self.position_mark= 1 
            elif event.trade_type == TradeType.SELL: self.position_mark= -1 
            if self.position_mark!=0: self.log_with_clock(logging.INFO, f"开仓，position_mark为：{self.position_mark}")
            else:self.log_with_clock(logging.ERROR,\
                    f"开仓后设置标志异常，没匹配到TradeType：{event.trade_type}，position_mark为：{self.position_mark}")
        #self.position_mark!=0 平仓，并标记
        elif self.position_mark != 0 :
            self.position_mark= 0
            self.isCoolingDown=True #平仓后进入冷却期；
            self.log_with_clock(logging.INFO, f"平仓，position_mark为：{self.position_mark}")

        #
        # 貌似有时候这个ignore没有成功执行；导致策略层跟踪器中还是有对应Order_id; 
        # @FIXME  ***或者由于下单与成交太接近，成单回调did_fill 早于 创建订单回调did_create，***
        #         ***导致更新状态出错或成单更新后再被设置了orderId，但无法再释放id。***
        #
        self.orderTracking.ignoreOrder(event.order_id) 

    
    def did_cancel_order(self, cancelled_event:OrderCancelledEvent):
        #移除策略层跟踪的对应订单；

        # super().did_cancel_order(cancelled_event) #顶层没做啥
        order_id=cancelled_event.order_id
        self.orderTracking.ignoreOrder(order_id)
        pass
        
    def did_create_buy_order(self,event:BuyOrderCreatedEvent ):
        #移除策略层跟踪的对应订单；

        order_id = event.order_id #获取订单id
        self.orderTracking.regBuyOrder(order_id)
        pass
    
    
    def did_create_sell_order(self,event:SellOrderCreatedEvent ):
        #移除策略层跟踪的对应订单；
        order_id = event.order_id #获取订单id
        self.orderTracking.regSellOrder(order_id)
        pass
    
    

