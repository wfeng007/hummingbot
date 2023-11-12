'''
Created on 2023年10月29日

@author: wfeng007
'''
from typing import Any, Dict, List, Set
import importlib
import sys
import logging
from decimal import Decimal
import pandas 



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

class SummOkxMarketBolling(SummOkxMarketAbs_m.SummOkxMarketAbs):
    '''
    SummBolling
    '''

    def __init__(self,connectors: Dict[str, ConnectorBase]):
        '''
        初始化
        '''
        # SummOkxMarketAbs_m=summ_scri_util.reload_module("SummOkxMarket")#重新加载，热加载
        super().__init__(connectors)

    #实现抽象方法：
    def strategyInitialize(self):
        self.bollingInitialize()
        pass
    def strategyDataParse(self,context:Context):
        self.bollingStrategyDataParse(context)
        pass

    def strategyEntrySignalAndProposeOrder(self,context:Context):
        self.bollingStrategyEntry(context)
        pass

    def strategyExitSignalAndProposeOrder(self,context:Context):
        self.bollingStrategyExit(context)
        pass
    ###


    #
    # 布林强盗策略
    #
    #趋势策略，持仓标志,<0空头仓 >0多头仓 0无仓位 #TODO 这里因为现货用简单仓位标志来记录仓位；
    position_mark=0
    isCoolingDown=False
    liqLen=20 #离场线参数；可以跟boll中线参数一致
    def bollingInitialize(self):

        self.order_amount = 0.01 

        #生成1个单例的bollingerDf,维持当前周期（period）的布林带信息等其实也可以记录其他信息；
        bollingerDFColumns = ["timestamp", "upperBand", "sma", "lowerBand","liq"]
        self.bollingerDf:pandas.DataFrame=pandas.DataFrame(columns=bollingerDFColumns)

        self.liqPeri=self.liqLen  #默认立场线，均线周期；
        # for connectorName,connector in self.connectors.items():
        #     self.logger().info(f"Connector:{connectorName}")
        
    
    def bollingStrategyDataParse(self,context:Context):
        '''
        bolling强盗策略的数据解析
        '''

        # context.nowCandlesDf

        #
        # 布林带；
        # 以收盘价建立当前布林带信息
        # 提取收盘价
        # closes = [float(closeP) for closeP in self.currentCandlesDf['close']]
        closes = [float(closeP) for closeP in context.nowCandlesDf['close']]
        context.candleCloseLs=closes
        upper_band, sma, lower_band=self.calc_bollinger_bands(closes,n=20 if len(closes)>= 20 else len(closes) )
        context.upper_band=upper_band
        context.sma=sma
        context.lower_band=lower_band
        #基于当前candles最后一行时间戳判断
        # nowTs=context.nowCandlesDf.iloc[-1]['timestamp']#使用candledf中的类型
        # isNewCandlePeriod:bool=False #"timestamp", "upperBand", "sma", "lowerBand"
        
        #根据是否candle新周期执行；
        # if nowTs not in self.bollingerDf['timestamp'].values:
        if context.isNewCandlePeriod:
            # 如果不存在,判单为candle新周期了
            # 创建一个新的 Series，写入bollingerDf
            # 
            new_row = pandas.Series({'timestamp': context.nowCandleTimestamp, 'upperBand': upper_band,'sma':sma,'lowerBand':lower_band,'liq':None})
            # self.bollingerDf=pandas.concat([self.bollingerDf,new_row.to_frame().T], ignore_index=True,axis=0)
            self.bollingerDf=pandas.concat([self.bollingerDf,pandas.DataFrame([new_row])], ignore_index=True,axis=0)

            # self.log_with_clock(logging.INFO, f"new Candle Period! isNewCandlePeriod:{isNewCandlePeriod}")
        else:
            # 如果已存在，根据条件设置
            # self.bollingerDf.loc[self.bollingerDf['timestamp'] == nowTs] = new_row #df 的行不能直接设置series，本身不一样；
            self.bollingerDf.loc[self.bollingerDf['timestamp'] == context.nowCandleTimestamp,["upperBand","sma","lowerBand"]] \
                = [upper_band,sma,lower_band]
            
        #只保留10个周期的bolling数据
        if len(self.bollingerDf) > 10: 
            # self.bollingerDf.drop(self.bollingerDf.index[0], inplace=True)
            catP=len(self.bollingerDf)-10
            self.bollingerDf=self.bollingerDf.iloc[catP:]
        
        context.nowBollingerDf=self.bollingerDf
        # self.log_with_clock(logging.INFO, f"bolling is ok!") 调试


    def bollingStrategyEntry(self,context:Context):
        ref_price=context.refPrice
        upper_band=context.upper_band
        sma=context.sma
        lower_band=context.lower_band
        proposalLs=context.proposalLs

        #冷却状态是否恢复
        #回到bb带中则冷却结束 @TODO 冷却恢复的条件可以更多,可以扩展。
        #等到下一个candle周期后再恢复
        if self.isCoolingDown and (ref_price<upper_band and ref_price>lower_band) and context.isNewCandlePeriod:
            self.isCoolingDown=False

        #
        #
        #入场开仓信号计算与开仓
        opSi=self.getBollingEntrySignal(ref_price,context.candleCloseLs,upper_band,sma,lower_band)

        
        if opSi>0:
            buy_price= None  #下单时使用bestprice
            buy_order = self._createOrderCandi(order_side=TradeType.BUY,price=buy_price) 
            proposalLs.append(buy_order)
        elif opSi<0:
            sell_price = None  #下单时使用bestprice
            sell_order =self._createOrderCandi(order_side=TradeType.SELL,price=sell_price) 
            proposalLs.append(sell_order)  
        else: #opSi==0 
            pass

        pass


    def bollingStrategyExit(self,context:Context):

        ref_price=context.refPrice
        upper_band=context.upper_band
        sma=context.sma
        lower_band=context.lower_band
        proposalLs=context.proposalLs

        # @TODO self._calcCloseSignal(ref_price,baseCandleDf,metrixDf,context)
        # @TODO 增加持仓情况的升级版，仓位管理模块化；
        # @TODO 整体以仓位状态为基础判断信号，来进行入离场操作的主流程实现。
        # 
        #离场线计算，离场均线是1某个周期均线的计算值；
        #
        #没有持仓，则为默认周期均线；
        #有持仓，则每经过1单位周期，则离周期-1来计算均线；即随着持仓递减；

        liqLen=20 #离场线参数；可以跟boll中线参数一致
        if  self.position_mark==0:
            self.liqPeri=liqLen
        elif self.position_mark != 0: #根据candle周期来计算判断
            #
            # 是否是同一个candle周期（而不是hbot的tick周期），其实时间短也好。
            # 简单算法：
            # self.liqPeri=self.liqPeri-1
            # 
            if context.isNewCandlePeriod: #每过1个candle周期时-1
                self.liqPeri=self.liqPeri-1

            #
            # @TODO BB带中也需要一定量的，liqPeri缩小，即离场线接近当前价格；或周期线缩小?
            #
            #
            #改用更保障盈利逻辑，如果是持多仓，价格高于upper_band 或
            #   如果是持空仓，价格低于lower_band  则更快速收敛离场线
            if (self.position_mark > 0 and ref_price > upper_band) \
                or (self.position_mark < 0 and ref_price < lower_band):
                self.liqPeri=self.liqPeri-1 #因为7为周期很容易到大上限；

            #如果持仓且价格在通道内部则不调整离场线；
            self.liqPeri=max(self.liqPeri,2)
            
        #    
        #离场移动平均线值，这里算法与bolling的sma没有用同一个，其实应该同一个算法实现；保证一致性；
        liqMeanV=context.nowCandlesDf.iloc[0:self.liqPeri]['close'].mean()  #candlesDf是时间是从大（新）->小（旧）排序
        # self.log_with_clock(logging.INFO, f"liqMeanV: {liqMeanV}")
        #有变化则放入说明candles已经变化
        self.bollingerDf.loc[self.bollingerDf['timestamp'] == context.nowCandleTimestamp,["liq"]] = [liqMeanV]
        #
        #离场平仓动作；滑单用bestAsk/bid okx只能用limit;
        #持多单时，(自适应出场均线低于布林通道上轨)，且价格下破自适应出场均线，平多；
        # if self.position_mark > 0 and liqMeanV < upper_band and  ref_price< liqMeanV: # ++ and liqMeanV>sma?
        if self.position_mark > 0 and ref_price< liqMeanV: 
            # 这里卖单替代平多
            sell_price = None #下单时使用bestprice
            sell_order = self._createOrderCandi(order_side=TradeType.SELL,price=sell_price) 
            proposalLs.append(sell_order)   

        # 持空单时，(自适应出场均线高于布林通道下轨)，且价格上破自适应出场均线，平空；
        # elif self.position_mark < 0 and liqMeanV > lower_band and  ref_price> liqMeanV:
        elif self.position_mark < 0 and  ref_price> liqMeanV:
            # 这里买单替代平空
            buy_price=None #下单时使用bestprice
            buy_order = self._createOrderCandi(order_side=TradeType.BUY,price=buy_price) 
            proposalLs.append(buy_order)

        pass

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

    # def _createOrderCandi(self,order_side:TradeType,price:Decimal=None):
    #     return OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,\
    #             order_side=order_side, amount=Decimal(self.order_amount), price=price)
    
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
        
    
    # 计算布林带
    def calc_bollinger_bands(self,closes, n=20, k=2):
        sma = sum(closes[-n:]) / n
        std = (sum((x - sma) ** 2 for x in closes[-n:]) / n) ** 0.5 #标准差？ 
        upper_band = sma + k * std
        lower_band = sma - k * std
        return (upper_band, sma, lower_band)
    

    
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
    
    

